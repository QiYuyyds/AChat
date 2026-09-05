"""Temporary probe — delete after debugging."""
from app.config import Settings
from app.config import get_settings


def test_probe_env_file(db):
    s = Settings(_env_file=None)
    print("\nPROBE _env_file=None neo4j_uri:", repr(s.neo4j_uri), "fields:", sorted(s.model_fields_set))
    s2 = Settings()
    print("PROBE plain Settings neo4j_uri:", repr(s2.neo4j_uri))
    gs_settings = get_settings()
    print("PROBE get_settings() neo4j_uri:", repr(gs_settings.neo4j_uri), "enable_graph:", gs_settings.enable_graph)
