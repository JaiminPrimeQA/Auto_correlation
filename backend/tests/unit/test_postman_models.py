from app.domain.enums import UnsupportedFeatureKind, VariableSource
from app.domain.postman_models import (
    CollectionInspection,
    PostmanFolder,
    PostmanVariable,
    UnsupportedFeature,
)


def test_postman_variable_defaults():
    v = PostmanVariable(name="token", source=VariableSource.UNRESOLVED, sensitive=True)
    assert v.locations == []


def test_collection_inspection_defaults_are_empty_and_serializable():
    inspection = CollectionInspection(collection_name="Demo")
    assert inspection.folders == []
    assert inspection.variables == []
    assert inspection.unresolved_variable_names == []
    assert inspection.request_count_estimate == 0
    assert inspection.target_domains == []
    assert inspection.domain_warnings == []
    assert inspection.unsupported_features == []
    assert inspection.warnings == []


def test_folder_and_unsupported_feature_construct():
    PostmanFolder(id="auth", name="Auth", path="Auth", request_count=2)
    UnsupportedFeature(
        kind=UnsupportedFeatureKind.INTERACTIVE_AUTH,
        detail="OAuth2 implicit grant requires a browser redirect.",
        location="Auth > Login",
    )
