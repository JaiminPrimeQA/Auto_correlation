"""Build a valid Apache JMeter 5.6.3 test plan from runs + approved rules.

The XML is constructed with ElementTree (never string concatenation). Element
names and properties are taken from JMeter-generated reference plans. All
user-controlled names/values are XML-escaped by ElementTree automatically.
"""

from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from typing import cast

from ..domain.enums import BodyMode, ExtractorMethod, LocationType, RuleState  # noqa: F401
from ..domain.models import (
    CorrelationRule,
    NormalizedExecution,
    NormalizedRequest,
    NormalizedRun,
    Pair,
    ValueOccurrence,
)
from ..utils.masking import is_sensitive_key
from ..utils.naming import sanitize_variable_name
from ..utils.xml import to_string
from .header_policy import NON_REPLAYED_HEADERS
from .jsonpath_support import jsonpath_values, scalar_text
from .run_aligner import align_runs

# See header_policy: shared with everything that proposes correlation targets.
_EXCLUDED_HEADERS = set(NON_REPLAYED_HEADERS)


def _set_cookie_names(execution: NormalizedExecution) -> set[str]:
    """Cookie names a response set; the Cookie Manager replays these itself."""
    if execution.response is None:
        return set()
    names = {c.name for c in execution.response.cookies}
    for h in execution.response.headers:
        if h.name.lower() == "set-cookie":
            name = h.value.split(";", 1)[0].partition("=")[0].strip()
            if name:
                names.add(name)
    return names


def _groovy_str(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _groovy_value(value: str) -> str:
    """'a${token}b' -> 'a' + vars.get('token') + 'b' (no ${} inside cached scripts)."""
    parts: list[str] = []
    pos = 0
    for m in re.finditer(r"\$\{(\w+)\}", value):
        if m.start() > pos:
            parts.append(_groovy_str(value[pos:m.start()]))
        parts.append(f"vars.get({_groovy_str(m.group(1))})")
        pos = m.end()
    if pos < len(value) or not parts:
        parts.append(_groovy_str(value[pos:]))
    return " + ".join(parts)


def _client_cookies(request: NormalizedRequest, server_cookies: set[str]) -> list[Pair]:
    """Request cookies the client set itself (not replayed by the Cookie Manager)."""
    return [c for c in request.cookies if c.name not in server_cookies]


@dataclass
class BuildOptions:
    num_threads: int = 1
    loops: int = 1
    parameterize_host: bool = True
    include_cache_manager: bool = True
    include_static_secrets: bool = False  # opt-in to embed real secret values
    externalize_secrets: bool = True
    keep_user_agent: bool = False  # User-Agent is runtime noise unless kept on purpose
    # JSON Extractors create these variables when their producer sampler runs.
    # Predeclaring them as __NOT_FOUND__ in UDV makes a healthy generated plan
    # look broken in JMeter before execution and is unnecessary.
    declare_correlation_vars: bool = False


@dataclass
class BuildResult:
    xml: str
    variables: list[str] = field(default_factory=list)
    externalized_secrets: list[str] = field(default_factory=list)  # property names
    required_properties: list[str] = field(default_factory=list)   # pass via -Jname=...
    replaced_consumers: int = 0
    extractor_counts: dict[str, int] = field(default_factory=dict)
    # Accepted consumers whose ${var} was NOT written into the rendered request.
    unmaterialized: list[dict] = field(default_factory=list)
    extractor_expressions: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Low-level element helpers
# --------------------------------------------------------------------------- #

def _sp(parent: ET.Element, name: str, text: str = "") -> ET.Element:
    e = ET.SubElement(parent, "stringProp", {"name": name})
    e.text = text
    return e


def _bp(parent: ET.Element, name: str, value: bool) -> ET.Element:
    e = ET.SubElement(parent, "boolProp", {"name": name})
    e.text = "true" if value else "false"
    return e


def _collection(parent: ET.Element, name: str) -> ET.Element:
    return ET.SubElement(parent, "collectionProp", {"name": name})


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #

class JmxBuilder:
    def __init__(self, options: BuildOptions | None = None) -> None:
        self.options = options or BuildOptions()

    def build(
        self,
        sequence_run: NormalizedRun,
        rules: list[CorrelationRule],
        comparison_run: NormalizedRun | None = None,
    ) -> BuildResult:
        active = [r for r in rules if r.state == RuleState.ENABLED]
        result = BuildResult(xml="", variables=[r.variable_name for r in active])

        # Producer response bodies, used to turn fragile fixed array indices
        # (e.g. $[106].merchantGUID) into stable, order-independent selectors.
        self._bodies = {
            e.id: e.response.parsed_body
            for e in sequence_run.executions
            if e.response is not None and e.response.parsed_body is not None
        }
        comparison_bodies = {}
        if comparison_run is not None:
            by_id = {e.id: e for e in comparison_run.executions}
            for pair in align_runs(sequence_run, comparison_run).pairs:
                other = by_id.get(pair.comparison_execution_id) if pair.comparison_execution_id is not None else None
                if other is not None and other.response is not None:
                    comparison_bodies[pair.baseline_execution_id] = other.response.parsed_body

        # Choose a common stable selector for sibling fields. A captured token
        # must never become the filter used to extract another dynamic field.
        for rule in active:
            expr = rule.extractor_expression
            if rule.extractor_method == ExtractorMethod.JSON_PATH:
                excluded = {
                    r.producer.canonical_path.rsplit(".", 1)[-1]
                    for r in active if r.producer.execution_id == rule.producer.execution_id
                    and not (r.producer.raw_value.isdigit() and len(r.producer.raw_value) <= 3)
                } if comparison_run is None else set()
                expr = _robustify_jsonpath(
                    expr, self._bodies.get(rule.producer.execution_id),
                    excluded_keys=excluded, allow_target=True,
                    comparison_body=comparison_bodies.get(rule.producer.execution_id),
                    require_comparison=comparison_run is not None,
                )
                matches = jsonpath_values(self._bodies.get(rule.producer.execution_id), expr)
                if not (0 < rule.match_number <= len(matches)) or scalar_text(matches[rule.match_number - 1]) != rule.producer.raw_value:
                    result.errors.append(f"JSON extractor '{rule.variable_name}' does not resolve to its selected source with match number {rule.match_number}.")
                if _ARRAY_LEAF.match(expr):
                    result.warnings.append(f"{rule.variable_name}: array order must remain stable; no unique stable selector was found.")
            elif rule.extractor_method == ExtractorMethod.JSON_JMESPATH:
                result.errors.append("JSON JMESPath is not supported by this exporter; select JSONPath instead.")
            result.extractor_expressions[rule.id] = expr
        self._expressions = result.extractor_expressions

        # Map producer execution -> rules to attach extractors.
        producers: dict[str, list[CorrelationRule]] = {}
        for r in active:
            producers.setdefault(r.producer.execution_id, []).append(r)

        # Map consumer execution -> list of (rule, consumer).
        consumers: dict[str, list[tuple[CorrelationRule, ValueOccurrence]]] = {}
        for r in active:
            for con in r.consumers:
                consumers.setdefault(con.execution_id, []).append((r, con))

        # Determine host parameterization.
        host_counts = Counter(e.request.host for e in sequence_run.executions if e.request.host)
        primary_host = host_counts.most_common(1)[0][0] if host_counts else ""
        primary_protocol = next(
            (e.request.protocol for e in sequence_run.executions if e.request.host == primary_host),
            "https",
        )

        root = ET.Element("jmeterTestPlan", {"version": "1.2", "properties": "5.0", "jmeter": "5.6.3"})
        top = ET.SubElement(root, "hashTree")

        self._test_plan(top)
        plan_tree = ET.SubElement(top, "hashTree")

        self._thread_group(plan_tree)
        tg_tree = ET.SubElement(plan_tree, "hashTree")

        self._cookie_manager(tg_tree)
        ET.SubElement(tg_tree, "hashTree")
        if self.options.include_cache_manager:
            self._cache_manager(tg_tree)
            ET.SubElement(tg_tree, "hashTree")

        udv_entries: list[tuple[str, str, str]] = []
        if self.options.parameterize_host and primary_host:
            udv_entries.append(("BASE_HOST", primary_host, "Base host"))
            udv_entries.append(("BASE_PROTOCOL", primary_protocol, "Base protocol"))

        # Samplers (in sequence order).
        sampler_elements: list[tuple[NormalizedExecution, ET.Element, ET.Element]] = []
        server_cookies: set[str] = set()  # names set via Set-Cookie so far: the Cookie Manager replays them
        for execution in sequence_run.executions:
            sub = consumers.get(execution.id, [])
            mutated, replaced, props, materialized = self._mutate_request(execution, sub, server_cookies)
            result.replaced_consumers += replaced
            for prop in props:
                if prop not in result.required_properties:
                    result.required_properties.append(prop)
                    result.externalized_secrets.append(prop)
            # Record any accepted consumer whose replacement did not land, so the
            # generator can refuse to emit a plan that claims a correlation it did
            # not actually apply (e.g. a JSON body left with the literal value).
            for rule, con in sub:
                key = (con.execution_id, con.location_type.value, con.canonical_path, rule.variable_name)
                if key not in materialized:
                    result.unmaterialized.append({
                        "variable": rule.variable_name,
                        "consumer_execution_id": con.execution_id,
                        "consumer": execution.item_name,
                        "location": con.location_type.value,
                        "path": con.canonical_path,
                    })

            sampler = self._http_sampler(execution, mutated, primary_host, primary_protocol)
            tg_tree.append(sampler)
            child_tree = ET.SubElement(tg_tree, "hashTree")
            self._header_manager(child_tree, mutated.headers)
            self._client_cookie_preprocessor(child_tree, _client_cookies(mutated, server_cookies))
            server_cookies |= _set_cookie_names(execution)

            for rule in producers.get(execution.id, []):
                self._extractor(child_tree, rule)
                result.extractor_counts[rule.extractor_method.value] = (
                    result.extractor_counts.get(rule.extractor_method.value, 0) + 1
                )
            sampler_elements.append((execution, sampler, child_tree))

        # A failed/skipped producer must never silently reuse captured values.
        if self.options.declare_correlation_vars:
            exec_name = {e.id: e.item_name for e in sequence_run.executions}
            for r in active:
                src = exec_name.get(r.producer.execution_id, "producer")
                desc = f"Set at runtime by 'Extract {r.variable_name}' on {src}."
                if r.variable_name not in {n for n, _, _ in udv_entries}:
                    udv_entries.append((r.variable_name, r.default_value, desc))

        # Insert UDV block right after managers (before samplers) by rebuilding order.
        if udv_entries:
            self._insert_udv(tg_tree, udv_entries)

        result.xml = to_string(root)
        return result

    # --- test plan / thread group / managers ---

    def _test_plan(self, parent: ET.Element) -> None:
        tp = ET.SubElement(parent, "TestPlan", {
            "guiclass": "TestPlanGui", "testclass": "TestPlan",
            "testname": "Baseline11 Auto-Correlated Plan", "enabled": "true",
        })
        _sp(tp, "TestPlan.comments", "Generated by Baseline11 Auto-Correlate")
        _bp(tp, "TestPlan.functional_mode", False)
        _bp(tp, "TestPlan.tearDown_on_shutdown", True)
        _bp(tp, "TestPlan.serialize_threadgroups", False)
        args = ET.SubElement(tp, "elementProp", {
            "name": "TestPlan.user_defined_variables", "elementType": "Arguments",
            "guiclass": "ArgumentsPanel", "testclass": "Arguments",
            "testname": "User Defined Variables", "enabled": "true",
        })
        _collection(args, "Arguments.arguments")
        _sp(tp, "TestPlan.user_define_classpath", "")

    def _thread_group(self, parent: ET.Element) -> None:
        tg = ET.SubElement(parent, "ThreadGroup", {
            "guiclass": "ThreadGroupGui", "testclass": "ThreadGroup",
            "testname": "Thread Group", "enabled": "true",
        })
        _sp(tg, "ThreadGroup.on_sample_error", "continue")
        lc = ET.SubElement(tg, "elementProp", {
            "name": "ThreadGroup.main_controller", "elementType": "LoopController",
            "guiclass": "LoopControlPanel", "testclass": "LoopController",
            "testname": "Loop Controller", "enabled": "true",
        })
        _bp(lc, "LoopController.continue_forever", False)
        _sp(lc, "LoopController.loops", str(self.options.loops))
        _sp(tg, "ThreadGroup.num_threads", str(self.options.num_threads))
        _sp(tg, "ThreadGroup.ramp_time", "1")
        _bp(tg, "ThreadGroup.scheduler", False)
        _sp(tg, "ThreadGroup.duration", "")
        _sp(tg, "ThreadGroup.delay", "")
        _bp(tg, "ThreadGroup.same_user_on_next_iteration", True)

    def _cookie_manager(self, parent: ET.Element) -> None:
        cm = ET.SubElement(parent, "CookieManager", {
            "guiclass": "CookiePanel", "testclass": "CookieManager",
            "testname": "HTTP Cookie Manager", "enabled": "true",
        })
        _collection(cm, "CookieManager.cookies")
        _bp(cm, "CookieManager.clearEachIteration", False)
        _bp(cm, "CookieManager.controlledByThreadGroup", False)

    def _cache_manager(self, parent: ET.Element) -> None:
        cm = ET.SubElement(parent, "CacheManager", {
            "guiclass": "CacheManagerGui", "testclass": "CacheManager",
            "testname": "HTTP Cache Manager", "enabled": "true",
        })
        _bp(cm, "clearEachIteration", True)
        _bp(cm, "useExpires", True)
        _bp(cm, "CacheManager.controlledByThread", False)

    def _insert_udv(self, tg_tree: ET.Element, entries: list[tuple[str, str, str]]) -> None:
        args = ET.Element("Arguments", {
            "guiclass": "ArgumentsPanel", "testclass": "Arguments",
            "testname": "User Defined Variables", "enabled": "true",
        })
        coll = _collection(args, "Arguments.arguments")
        for name, value, desc in entries:
            ep = ET.SubElement(coll, "elementProp", {"name": name, "elementType": "Argument"})
            _sp(ep, "Argument.name", name)
            _sp(ep, "Argument.value", value)
            _sp(ep, "Argument.metadata", "=")
            if desc:
                _sp(ep, "Argument.desc", desc)
        # Insert UDV + its hashTree at the front of the thread-group tree.
        tg_tree.insert(0, ET.Element("hashTree"))
        tg_tree.insert(0, args)

    # --- HTTP sampler ---

    def _http_sampler(
        self,
        execution: NormalizedExecution,
        req,
        primary_host: str,
        primary_protocol: str,
    ) -> ET.Element:
        sampler = ET.Element("HTTPSamplerProxy", {
            "guiclass": "HttpTestSampleGui", "testclass": "HTTPSamplerProxy",
            "testname": self._sampler_name(execution), "enabled": "true",
        })
        args_ep = ET.SubElement(sampler, "elementProp", {
            "name": "HTTPsampler.Arguments", "elementType": "Arguments",
            "guiclass": "HTTPArgumentsPanel", "testclass": "Arguments",
            "testname": "User Defined Variables", "enabled": "true",
        })
        coll = _collection(args_ep, "Arguments.arguments")

        post_body_raw = False
        if req.body_mode in (BodyMode.JSON, BodyMode.RAW, BodyMode.TEXT, BodyMode.XML) and req.raw_body is not None:
            post_body_raw = True
            arg = ET.SubElement(coll, "elementProp", {"name": "", "elementType": "HTTPArgument"})
            _bp(arg, "HTTPArgument.always_encode", False)
            _sp(arg, "Argument.value", req.raw_body)
            _sp(arg, "Argument.metadata", "=")
        elif req.body_mode == BodyMode.URLENCODED or req.body_mode == BodyMode.FORMDATA:
            for f in req.form_data:
                self._http_argument(coll, f.name, f.value, encode=True)
        else:
            # GET/DELETE query parameters become sampler arguments.
            for q in req.query:
                self._http_argument(coll, q.name, q.value, encode=False)

        use_var_host = self.options.parameterize_host and req.host == primary_host and primary_host
        _sp(sampler, "HTTPSampler.domain", "${BASE_HOST}" if use_var_host else req.host)
        _sp(sampler, "HTTPSampler.port", str(req.port) if req.port else "")
        _sp(sampler, "HTTPSampler.protocol", "${BASE_PROTOCOL}" if use_var_host else req.protocol)
        _sp(sampler, "HTTPSampler.contentEncoding", "")
        _sp(sampler, "HTTPSampler.path", req.path or "/")
        _sp(sampler, "HTTPSampler.method", req.method)
        _bp(sampler, "HTTPSampler.follow_redirects", True)
        _bp(sampler, "HTTPSampler.auto_redirects", False)
        _bp(sampler, "HTTPSampler.use_keepalive", True)
        _bp(sampler, "HTTPSampler.DO_MULTIPART_POST", req.body_mode == BodyMode.FORMDATA)
        _sp(sampler, "HTTPSampler.embedded_url_re", "")
        _sp(sampler, "HTTPSampler.connect_timeout", "")
        _sp(sampler, "HTTPSampler.response_timeout", "")
        if post_body_raw:
            _bp(sampler, "HTTPSampler.postBodyRaw", True)
        return sampler

    def _http_argument(self, coll: ET.Element, name: str, value: str, *, encode: bool) -> None:
        arg = ET.SubElement(coll, "elementProp", {"name": name, "elementType": "HTTPArgument"})
        _bp(arg, "HTTPArgument.always_encode", encode)
        _sp(arg, "Argument.value", value)
        _sp(arg, "Argument.metadata", "=")
        _bp(arg, "HTTPArgument.use_equals", True)
        _sp(arg, "Argument.name", name)

    def _sampler_name(self, execution: NormalizedExecution) -> str:
        base = execution.item_name or f"{execution.method} {execution.request.path}"
        return f"{execution.original_index + 1:02d} {base}"

    # --- header manager ---

    def _client_cookie_preprocessor(self, parent: ET.Element, cookies: list[Pair]) -> None:
        """Send cookies the client set itself (e.g. `Cookie: token=...`).

        A Cookie header in a Header Manager is overwritten by JMeter's Cookie
        Manager whenever the server has set cookies too, so the cookies are
        added to the Cookie Manager right before the request instead."""
        if not cookies:
            return
        entries = ", ".join(f"[{_groovy_str(c.name)}, {_groovy_value(c.value)}]" for c in cookies)
        script = (
            "// Cookies this request sets itself (not received via Set-Cookie).\n"
            "import org.apache.jmeter.protocol.http.control.Cookie\n"
            "def cm = sampler.getCookieManager()\n"
            "if (cm == null) { log.warn('No HTTP Cookie Manager: client cookies not sent'); return }\n"
            "def secure = sampler.getProtocol() == 'https'\n"
            f"[{entries}].each {{ c -> cm.add(new Cookie(c[0], c[1] ?: '', sampler.getDomain(), '/', secure, 0)) }}\n"
        )
        names = ", ".join(c.name for c in cookies)
        e = ET.SubElement(parent, "JSR223PreProcessor", {
            "guiclass": "TestBeanGUI", "testclass": "JSR223PreProcessor",
            "testname": f"Set client cookies ({names})", "enabled": "true",
        })
        _sp(e, "scriptLanguage", "groovy")
        _sp(e, "parameters", "")
        _sp(e, "filename", "")
        _sp(e, "cacheKey", "true")
        _sp(e, "script", script)
        ET.SubElement(parent, "hashTree")

    def _header_manager(self, parent: ET.Element, headers: list[Pair]) -> None:
        excluded = _EXCLUDED_HEADERS if self.options.keep_user_agent else _EXCLUDED_HEADERS | {"user-agent"}
        keep = [h for h in headers if h.name.lower() not in excluded]
        if not keep:
            return
        hm = ET.SubElement(parent, "HeaderManager", {
            "guiclass": "HeaderPanel", "testclass": "HeaderManager",
            "testname": "HTTP Header Manager", "enabled": "true",
        })
        coll = _collection(hm, "HeaderManager.headers")
        for h in keep:
            ep = ET.SubElement(coll, "elementProp", {"name": h.name, "elementType": "Header"})
            _sp(ep, "Header.name", h.name)
            _sp(ep, "Header.value", h.value)
        ET.SubElement(parent, "hashTree")

    # --- extractors ---

    def _extractor(self, parent: ET.Element, rule: CorrelationRule) -> None:
        method = rule.extractor_method
        if method in (ExtractorMethod.JSON_PATH, ExtractorMethod.JSON_JMESPATH):
            self._json_extractor(parent, rule)
        elif method == ExtractorMethod.XPATH2:
            self._xpath2_extractor(parent, rule)
        elif method == ExtractorMethod.BOUNDARY:
            self._boundary_extractor(parent, rule)
        elif method == ExtractorMethod.JSR223:
            self._jsr223_extractor(parent, rule)
        else:
            self._regex_extractor(parent, rule)
        ET.SubElement(parent, "hashTree")

    def _json_extractor(self, parent: ET.Element, rule: CorrelationRule) -> None:
        e = ET.SubElement(parent, "JSONPostProcessor", {
            "guiclass": "JSONPostProcessorGui", "testclass": "JSONPostProcessor",
            "testname": f"Extract {rule.variable_name}", "enabled": "true",
        })
        expr = self._expressions[rule.id]
        _sp(e, "JSONPostProcessor.referenceNames", rule.variable_name)
        _sp(e, "JSONPostProcessor.jsonPathExprs", expr)
        _sp(e, "JSONPostProcessor.match_numbers", str(rule.match_number))
        _sp(e, "JSONPostProcessor.defaultValues", rule.default_value)
        _bp(e, "JSONPostProcessor.compute_concat", False)

    def _xpath2_extractor(self, parent: ET.Element, rule: CorrelationRule) -> None:
        e = ET.SubElement(parent, "XPath2Extractor", {
            "guiclass": "XPath2ExtractorGui", "testclass": "XPath2Extractor",
            "testname": f"Extract {rule.variable_name}", "enabled": "true",
        })
        _sp(e, "XPathExtractor2.default", rule.default_value)
        _sp(e, "XPathExtractor2.refname", rule.variable_name)
        _sp(e, "XPathExtractor2.xpathQuery", rule.extractor_expression)
        _sp(e, "XPathExtractor2.namespaces", "")
        _sp(e, "XPathExtractor2.matchNumber", str(rule.match_number))
        _bp(e, "XPathExtractor2.fragment", False)

    def _regex_extractor(self, parent: ET.Element, rule: CorrelationRule) -> None:
        use_headers = rule.producer.location_type in (LocationType.HEADER, LocationType.COOKIE)
        e = ET.SubElement(parent, "RegexExtractor", {
            "guiclass": "RegexExtractorGui", "testclass": "RegexExtractor",
            "testname": f"Extract {rule.variable_name}", "enabled": "true",
        })
        _sp(e, "RegexExtractor.useHeaders", "true" if use_headers else "false")
        _sp(e, "RegexExtractor.refname", rule.variable_name)
        _sp(e, "RegexExtractor.regex", rule.extractor_expression)
        _sp(e, "RegexExtractor.template", "$1$")
        _sp(e, "RegexExtractor.default", rule.default_value)
        _sp(e, "RegexExtractor.match_number", str(rule.match_number))
        _bp(e, "RegexExtractor.default_empty_value", False)

    def _boundary_extractor(self, parent: ET.Element, rule: CorrelationRule) -> None:
        use_headers = rule.producer.location_type in (LocationType.HEADER, LocationType.COOKIE)
        e = ET.SubElement(parent, "BoundaryExtractor", {
            "guiclass": "BoundaryExtractorGui", "testclass": "BoundaryExtractor",
            "testname": f"Extract {rule.variable_name}", "enabled": "true",
        })
        _sp(e, "BoundaryExtractor.useHeaders", "true" if use_headers else "false")
        _sp(e, "BoundaryExtractor.refname", rule.variable_name)
        lb, rb = self._boundaries(rule.extractor_expression)
        _sp(e, "BoundaryExtractor.lboundary", lb)
        _sp(e, "BoundaryExtractor.rboundary", rb)
        _sp(e, "BoundaryExtractor.default", rule.default_value)
        _sp(e, "BoundaryExtractor.match_number", str(rule.match_number))

    def _boundaries(self, expr: str) -> tuple[str, str]:
        # expr may be "left|||right"; otherwise derive conservative boundaries.
        if "|||" in expr:
            lb, rb = expr.split("|||", 1)
            return lb, rb
        return expr, ""

    def _jsr223_extractor(self, parent: ET.Element, rule: CorrelationRule) -> None:
        e = ET.SubElement(parent, "JSR223PostProcessor", {
            "guiclass": "TestBeanGUI", "testclass": "JSR223PostProcessor",
            "testname": f"Extract {rule.variable_name} (advanced)", "enabled": "true",
        })
        _sp(e, "scriptLanguage", "groovy")
        _sp(e, "parameters", "")
        _sp(e, "filename", "")
        _sp(e, "cacheKey", "true")
        _sp(e, "script", rule.extractor_expression)

    def _captured_value(self, rule: CorrelationRule) -> str:
        """Baseline value the extractor produced, used as the UDV default."""
        if rule.producer.raw_value:
            return rule.producer.raw_value
        body = getattr(self, "_bodies", {}).get(rule.producer.execution_id)
        if body is not None and rule.extractor_method in (ExtractorMethod.JSON_PATH, ExtractorMethod.JSON_JMESPATH):
            from .value_indexer import flatten_json

            table = dict(flatten_json(body))
            v = table.get(rule.extractor_expression)
            if v is not None:
                return str(v)
        return ""

    # --- request mutation (consumer substitution + secret externalisation) ---

    def _mutate_request(
        self,
        execution: NormalizedExecution,
        subs: list[tuple[CorrelationRule, ValueOccurrence]],
        server_cookies: set[str] | None = None,
    ):
        req = copy.deepcopy(execution.request)
        replaced = 0
        property_names: list[str] = []
        materialized: set[tuple[str, str, str]] = set()

        def _mark(con: ValueOccurrence) -> None:
            materialized.add((con.execution_id, con.location_type.value, con.canonical_path))

        # Apply consumer substitutions grouped by location.
        json_subs: list[tuple[ValueOccurrence, str]] = []  # (consumer, var)
        for rule, con in subs:
            token = f"${{{rule.variable_name}}}"
            loc = con.location_type
            if loc == LocationType.PATH:
                idx = int(con.canonical_path.strip("[]"))
                if 0 <= idx < len(req.path_segments):
                    req.path_segments[idx] = (con.wrapper or "") + token
                    req.path = "/" + "/".join(req.path_segments)
                    replaced += 1
                    _mark(con)
            elif loc == LocationType.QUERY:
                n = self._replace_pair(req.query, con, token)
                replaced += n
                if n:
                    _mark(con)
            elif loc == LocationType.HEADER:
                n = self._replace_pair(req.headers, con, token, wrapper=con.wrapper)
                replaced += n
                if n:
                    _mark(con)
            elif loc == LocationType.COOKIE:
                n = self._replace_pair(req.cookies, con, token)
                replaced += n
                if n:
                    _mark(con)
            elif loc == LocationType.FORM:
                n = self._replace_pair(req.form_data, con, token)
                replaced += n
                if n:
                    _mark(con)
            elif loc == LocationType.JSON_BODY:
                json_subs.append((con, rule.variable_name))
            elif loc == LocationType.TEXT_BODY and req.raw_body:
                new = req.raw_body.replace(con.raw_value, token, 1)
                if new != req.raw_body:
                    req.raw_body = new
                    replaced += 1
                    _mark(con)

        if json_subs and req.parsed_body is not None:
            done_paths = self._apply_json_subs(req, json_subs)
            replaced += len(done_paths)
            for con, _var in json_subs:
                if con.canonical_path in done_paths:
                    _mark(con)

        # External-credential handling. A sensitive-KEY header (Authorization,
        # x-tokenguid, api-key, ...) with no correlated producer is supplied at
        # runtime via a JMeter property function: x-tokenguid: ${__P(x_tokenguid,)}.
        # The real secret is NEVER embedded, and a masked/placeholder value is
        # NEVER written into the executable plan. Only sensitive KEYS are
        # externalised; secret-SHAPED values under innocuous keys are left for
        # manual review (see classification.py), not auto-externalised.
        if self.options.externalize_secrets and not self.options.include_static_secrets:
            for h in req.headers:
                if h.name.lower() in _EXCLUDED_HEADERS:
                    continue  # dropped by the header manager anyway
                if "${" in h.value:
                    continue  # already carries a correlated variable reference
                if is_sensitive_key(h.name):
                    prop = sanitize_variable_name(h.name)
                    h.value = f"${{__P({prop},)}}"
                    property_names.append(prop)

        # Verify the FINAL value at each requested location. A later rule may
        # have overwritten an earlier substitution, or header filtering may
        # remove it; a successful intermediate setter is not proof of export.
        verified = set()
        for rule, con in subs:
            token = f"${{{rule.variable_name}}}"
            expected = (con.wrapper or "") + token
            values: list[object] = []
            if con.location_type == LocationType.JSON_BODY:
                values = jsonpath_values(req.parsed_body, con.canonical_path)
            elif con.location_type == LocationType.PATH:
                idx = int(con.canonical_path.strip("[]"))
                values = cast("list[object]", req.path_segments[idx:idx + 1])
            elif con.location_type in (LocationType.QUERY, LocationType.HEADER, LocationType.FORM):
                pairs = {LocationType.QUERY: req.query, LocationType.HEADER: req.headers, LocationType.FORM: req.form_data}[con.location_type]
                values = [p.value for p in pairs if p.name == (con.key or con.canonical_path)]
                if con.location_type == LocationType.HEADER and (
                    (con.key or con.canonical_path).lower() in _EXCLUDED_HEADERS
                    or ((con.key or con.canonical_path).lower() == "user-agent" and not self.options.keep_user_agent)
                ):
                    values = []
            elif con.location_type == LocationType.COOKIE:
                # Only a client-set cookie is emitted (as a Cookie header).
                values = [c.value for c in _client_cookies(req, server_cookies or set())
                          if c.name == (con.key or con.canonical_path)]
            elif con.location_type == LocationType.TEXT_BODY and token in (req.raw_body or ""):
                values = [expected]
            if expected in values:
                verified.add((con.execution_id, con.location_type.value, con.canonical_path, rule.variable_name))
        return req, len(verified), property_names, verified

    def _replace_pair(self, pairs: list[Pair], con, token: str, wrapper: str | None = None) -> int:
        for p in pairs:
            if p.name == (con.key or con.canonical_path) and p.value == con.raw_value:
                p.value = (wrapper or "") + token
                return 1
        # fall back to name-only match
        for p in pairs:
            if p.name == (con.key or con.canonical_path):
                p.value = (wrapper or "") + token
                return 1
        return 0

    def _apply_json_subs(self, req, json_subs: list[tuple[ValueOccurrence, str]]) -> set[str]:
        """Serialise ${var} into the JSON body. Returns the canonical paths that
        were actually substituted (so the caller can detect ones that were not)."""
        body = copy.deepcopy(req.parsed_body)
        markers: list[tuple[str, str, bool]] = []  # (marker, replacement, numeric_context)
        done: set[str] = set()
        for index, (con, var) in enumerate(json_subs):
            # ASCII sentinel unlikely to collide with real body content.
            marker = f"@@B11_SENTINEL_{index}_{var}@@"
            numeric = con.data_type.value == "number"
            if _set_json_path(body, con.canonical_path, marker):
                markers.append((marker, (con.wrapper or "") + f"${{{var}}}", numeric))
                done.add(con.canonical_path)
        text = json.dumps(body, ensure_ascii=False)
        for marker, token, numeric in markers:
            if numeric:
                # Original leaf was numeric: drop the surrounding quotes.
                text = text.replace(f'"{marker}"', token)
            else:
                text = text.replace(marker, token)
        req.raw_body = text
        req.parsed_body = copy.deepcopy(body)
        for con, var in json_subs:
            _set_json_path(req.parsed_body, con.canonical_path, (con.wrapper or "") + f"${{{var}}}")
        return done


# --------------------------------------------------------------------------- #
# Robust array selectors: turn $[106].merchantGUID into
# $[?(@.merchantID==6)].merchantGUID so the right element is found regardless
# of list order (Jayway JsonPath filter, supported by JMeter's JSON Extractor).
# --------------------------------------------------------------------------- #

_ARRAY_LEAF = re.compile(r"^(?P<prefix>.*)\[(?P<idx>\d+)\]\.(?P<target>[A-Za-z_]\w*)$")


def _robustify_jsonpath(
    expr: str, body: object, *, excluded_keys: set[str] | None = None,
    allow_target: bool = False, comparison_body: object = None,
    require_comparison: bool = False,
) -> str:
    if body is None:
        return expr
    m = _ARRAY_LEAF.match(expr)
    if not m:
        return expr
    prefix, idx, target = m.group("prefix"), int(m.group("idx")), m.group("target")
    arr = _navigate(body, prefix)
    if not isinstance(arr, list) or not (0 <= idx < len(arr)):
        return expr
    obj = arr[idx]
    if not isinstance(obj, dict):
        return expr
    other = _navigate(comparison_body, prefix) if require_comparison else None
    anchor = _choose_anchor(arr, obj, target, excluded_keys, allow_target, other, require_comparison)
    if anchor is None:
        return expr
    key, val = anchor
    if isinstance(val, str):
        safe = val.replace("\\", "\\\\").replace("'", "\\'")
        cond = f"@.{key}=='{safe}'"
    else:
        cond = f"@.{key}=={val}"
    return f"{prefix}[?({cond})].{target}"


def _navigate(body: object, prefix: str) -> object:
    """Resolve a dotted prefix like '$' or '$.data.items' to a node."""
    cur = body
    for tok in [t for t in re.split(r"[.\[\]]", prefix) if t and t != "$"]:
        if tok.isdigit() and isinstance(cur, list) and int(tok) < len(cur):
            cur = cur[int(tok)]
        elif isinstance(cur, dict) and tok in cur:
            cur = cur[tok]
        else:
            return None
    return cur


def _choose_anchor(
    arr: list, obj: dict, target: str, excluded_keys: set[str] | None = None,
    allow_target: bool = False, comparison: object = None, require_comparison: bool = False,
) -> tuple[str, object] | None:
    """Pick a sibling field whose value uniquely identifies this element."""
    def unique(key: str, value: object) -> bool:
        matches = sum(1 for o in arr if isinstance(o, dict) and o.get(key) == value) == 1
        if require_comparison:
            matches = matches and isinstance(comparison, list) and sum(
                1 for o in comparison if isinstance(o, dict) and o.get(key) == value
            ) == 1
        return matches

    scalar = {
        k: v for k, v in obj.items()
        if (allow_target or k != target) and k not in (excluded_keys or set())
        and re.fullmatch(r"[A-Za-z_]\w*", k)
        and isinstance(v, (str, int, float)) and not isinstance(v, bool) and v not in (None, "")
        and not (isinstance(v, str) and (";" in v or "${" in v or "\n" in v or "\r" in v))
    }
    # Prefer an id-like key, then any unique scalar. Numbers/short strings first.
    id_like = sorted(
        [k for k in scalar if k.lower().endswith(("id", "guid", "uuid"))],
        key=lambda k: (not isinstance(scalar[k], (int, float)), len(str(scalar[k]))),
    )
    for key in id_like + list(scalar):
        if unique(key, scalar[key]):
            return key, scalar[key]
    return None


# --------------------------------------------------------------------------- #
# JSONPath setter (matches the grammar produced by value_indexer.flatten_json)
# --------------------------------------------------------------------------- #

_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\['([^']*)'\]|\[(\d+)\]")


def _parse_path(path: str) -> list[object]:
    tokens: list[object] = []
    for m in _TOKEN.finditer(path):
        if m.group(1) is not None:
            tokens.append(m.group(1))
        elif m.group(2) is not None:
            tokens.append(m.group(2))
        else:
            tokens.append(int(m.group(3)))
    return tokens


def _set_json_path(obj: object, path: str, value: object) -> bool:
    tokens = _parse_path(path)
    if not tokens:
        return False
    cur = obj
    for tok in tokens[:-1]:
        if isinstance(tok, int) and isinstance(cur, list) and 0 <= tok < len(cur):
            cur = cur[tok]
        elif isinstance(tok, str) and isinstance(cur, dict) and tok in cur:
            cur = cur[tok]
        else:
            return False
    last = tokens[-1]
    if isinstance(last, int) and isinstance(cur, list) and 0 <= last < len(cur):
        cur[last] = value
        return True
    if isinstance(last, str) and isinstance(cur, dict) and last in cur:
        cur[last] = value
        return True
    return False
