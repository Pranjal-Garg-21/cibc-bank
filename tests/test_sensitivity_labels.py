from trustissues.common import config
from trustissues.factory.ingest.dictionary import derive_labels, load_curate_cfg, load_overrides


def test_derived_columns_inherit_source_label():
    tables = {"customers": {"dob_raw": "direct_pii", "full_name_raw": "direct_pii", "postal_code_raw": "quasi_identifier",
                            "employer_name": "quasi_identifier", "primary_phone_raw": "direct_pii"}}
    curated = {"customers": ["std_name_first", "std_name_key", "std_dob", "std_dob_ambiguous", "std_postal", "std_fsa",
                             "std_employer", "std_phone_primary_e164", "std_phone_primary_masked", "dob_raw__raw"]}
    cfg = {"identity_fields": {"customers": {
        "name": {"col": "full_name_raw"}, "dob": "dob_raw", "postal": "postal_code_raw", "employer": "employer_name",
        "phones": {"primary": "primary_phone_raw"}}}}
    derive_labels(tables, curated, cfg)
    c = tables["customers"]
    assert c["std_name_first"] == c["std_name_key"] == "direct_pii"
    assert c["std_dob"] == c["std_dob_ambiguous"] == "direct_pii"
    assert c["std_phone_primary_e164"] == c["std_phone_primary_masked"] == "direct_pii"
    assert c["std_postal"] == c["std_fsa"] == c["std_employer"] == "quasi_identifier"
    assert c["dob_raw__raw"] == "direct_pii"


def test_unknown_source_label_defaults_to_restricted():
    tables = {"t": {}}
    derive_labels(tables, {"t": ["x__raw"]}, {})
    assert tables["t"]["x__raw"] == "unclassified"


def test_overrides_file_loads_and_has_a_reason():
    o = load_overrides()
    assert o["deposit_accounts.overdraft_dpd"]["label"] == "none"
    assert all(v.get("reason") and v.get("decided_by") for v in o.values())


def test_dictionary_step_works_without_curate_config(tmp_path, monkeypatch):
    """Ingest must stand alone: a missing config/curate.yaml is not an error."""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    assert load_curate_cfg() == {}
    assert load_overrides() == {}


def test_dq_flags_and_derived_columns_get_labels():
    tables = {"deposit_accounts": {"src_customer_ref": "direct_pii"}, "external": {}}
    curated = {"deposit_accounts": ["cif10", "dq_non_cad"], "external": ["dq_inquiries_inconsistent"]}
    dq = {"derived_columns": [{"table": "curated.deposit_accounts", "column": "cif10", "from": "src_customer_ref"}]}
    derive_labels(tables, curated, {}, dq)
    assert tables["deposit_accounts"]["cif10"] == "direct_pii"            # a derived key is as sensitive as its source
    assert tables["deposit_accounts"]["dq_non_cad"] == "none"             # a data-quality flag is not personal data
    assert tables["external"]["dq_inquiries_inconsistent"] == "none"
