import duckdb
import openpyxl
import pytest

from trustissues.ask.data.semantic import SCHEMAS
from trustissues.ask.data.semantic_adapters import build_dev_adapters
from trustissues.common.config import REPO_ROOT
from trustissues.common.interfaces import apply_stubs
from trustissues.factory.curate.build_curated import kind


@pytest.fixture
def con():
    c=duckdb.connect(':memory:')
    c.execute('CREATE SCHEMA curated')
    apply_stubs(c)
    workbook=openpyxl.load_workbook(REPO_ROOT/'docs'/'CIBC Hackathon - Data Dictionary.xlsx',read_only=True,data_only=True)
    rows=workbook['Columns'].iter_rows(values_only=True)
    header=next(rows)
    schema={}
    for values in rows:
        row=dict(zip(header,values))
        schema.setdefault(row['Table'],[]).append(row)
    workbook.close()
    types={'text':'VARCHAR','int':'BIGINT','date':'DATE','timestamp':'TIMESTAMP','double':'DOUBLE','boolean':'BOOLEAN'}
    for table in ['collections_cases','agents','promises_to_pay','contact_history','card_accounts','loan_accounts',
                  'deposit_accounts','account_monthly_snapshot']:
        fields=', '.join(f'"{col["Column"]}" {types[kind(col["Type"])]}' for col in schema[table])
        c.execute(f'CREATE TABLE curated.{table} ({fields})')
    yield c
    c.close()


def test_adapter_shapes_match_contracts_and_preserve_missing_site(con):
    con.execute("INSERT INTO curated.collections_cases (case_id,coll_customer_ref,current_dpd,current_bucket) VALUES ('C','CRM1',35,'1-30')")
    con.execute("INSERT INTO gold.customer_xref (source_system,source_key,golden_customer_id,status) VALUES ('crm','CRM1','G1','linked')")
    con.execute("INSERT INTO curated.promises_to_pay (ptp_id,case_id,agent_id) VALUES ('P','C','unknown')")
    con.execute("INSERT INTO curated.contact_history (contact_id,case_id,channel,channel_v2) VALUES ('H','C',NULL,'call')")
    build_dev_adapters(con,development_database=True)
    for table,cols in SCHEMAS.items():
        actual={r[0] for r in con.execute(f'DESCRIBE {table}').fetchall()}
        assert actual == set(cols)
    assert con.execute('SELECT reported_bucket,canonical_bucket FROM gold.semantic_cases').fetchone() == ('1-30','31-60')
    assert con.execute('SELECT agent_site FROM gold.semantic_promises').fetchone() == ('self_serve',)
    assert con.execute('SELECT channel FROM gold.semantic_contacts').fetchone() == ('call',)


def test_adapter_refuses_ambiguous_joins_and_requires_opt_in(con):
    with pytest.raises(ValueError):build_dev_adapters(con)
    con.execute("INSERT INTO curated.agents (agent_id) VALUES ('A'),('A')")
    with pytest.raises(ValueError,match='Ambiguous row grain'):
        build_dev_adapters(con,development_database=True)
    assert con.execute("SELECT count(*) FROM information_schema.views WHERE table_name LIKE 'semantic_%'").fetchone()[0] == 0


def test_adapter_does_not_write_real_warehouse(tmp_path):
    c=duckdb.connect(str(tmp_path/'trustissues.duckdb'))
    with pytest.raises(ValueError,match='worker warehouse'):
        build_dev_adapters(c,development_database=True)
    c.close()
