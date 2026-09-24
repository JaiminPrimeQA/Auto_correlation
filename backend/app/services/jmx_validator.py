"""Structural validation of generated JMX before download."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

_VAR_REF = re.compile(r"\$\{([^}]+)\}")
_REFNAME_PROPS = {
    "JSONPostProcessor.referenceNames",
    "RegexExtractor.refname",
    "BoundaryExtractor.refname",
    "XPathExtractor2.refname",
}


@dataclass
class ValidationResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    defined_variables: list[str] = field(default_factory=list)
    used_variables: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)


def validate_jmx(xml_text: str) -> ValidationResult:
    result = ValidationResult()

    if "@@B11_SENTINEL_" in xml_text or chr(0) in xml_text:
        result.fail("Generated XML contains an unresolved internal placeholder.")

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        result.fail(f"Generated XML does not parse: {exc}")
        return result

    if root.tag != "jmeterTestPlan":
        result.fail("Root element is not <jmeterTestPlan>.")
        return result

    children = list(root)
    if len(children) != 1 or children[0].tag != "hashTree":
        result.fail("Test plan root must contain exactly one <hashTree>.")
        return result

    _check_pairing(children[0], result)

    defined: set[str] = set()
    for prop in root.iter("stringProp"):
        if prop.get("name") in _REFNAME_PROPS and prop.text:
            defined.add(prop.text.strip())
        if prop.get("name") == "Argument.name" and prop.text:
            defined.add(prop.text.strip())
    result.defined_variables = sorted(defined)

    used: set[str] = set()
    for prop in root.iter("stringProp"):
        if not prop.text:
            continue
        for m in _VAR_REF.finditer(prop.text):
            name = m.group(1)
            if name.startswith("__"):  # JMeter function, not a variable
                continue
            used.add(name)
    result.used_variables = sorted(used)

    for name in used:
        if name not in defined:
            result.fail(f"Variable '${{{name}}}' is used but never defined by an extractor or UDV.")

    return result


def _check_pairing(hash_tree: ET.Element, result: ValidationResult) -> None:
    """Every test element in a hashTree must be immediately followed by a hashTree."""
    kids = list(hash_tree)
    i = 0
    while i < len(kids):
        node = kids[i]
        if node.tag == "hashTree":
            # A hashTree with no preceding element is malformed.
            result.fail("Found a <hashTree> without a preceding test element.")
            i += 1
            continue
        # node is a test element; next must be a hashTree
        if i + 1 >= len(kids) or kids[i + 1].tag != "hashTree":
            result.fail(f"Test element <{node.tag}> is not followed by a <hashTree>.")
            i += 1
            continue
        _check_pairing(kids[i + 1], result)
        i += 2
