import duckdb
import pytest

from trustissues.ask.data.execute import QueryTimeout, execute
from trustissues.ask.data.validator import QueryPolicy, ValidatedQuery, validate


def test_read_only_file_and_raw_sql_rejected(tmp_path):
    path = tmp_path / "execute_fixture.duckdb"
    c = duckdb.connect(str(path))
    c.execute("CREATE SCHEMA gold; CREATE TABLE gold.t (v INTEGER); INSERT INTO gold.t VALUES (42)")
    c.close()
    q = validate("SELECT v FROM gold.t", QueryPolicy("analyst", {"gold.t": {"v": "INTEGER"}}))
    assert execute(q, database=path).rows == ((42,),)
    with pytest.raises(TypeError):
        execute("DELETE FROM gold.t", database=path)
    c = duckdb.connect(str(path))
    with pytest.raises(ValueError):
        execute(q, con=c)
    c.close()


def test_timeout_and_next_query_survives():
    c = duckdb.connect(":memory:")
    # Internal trusted query used to exercise interrupt; range is intentionally blocked by validator.
    q = ValidatedQuery("SELECT sum(a.i*b.i) FROM range(1000000000) a(i), range(1000000000) b(i)", {}, 1)
    with pytest.raises(QueryTimeout):
        execute(q, con=c, timeout_seconds=.01)
    assert execute(ValidatedQuery("SELECT 42", {}, 1), con=c).rows == ((42,),)
    c.close()
