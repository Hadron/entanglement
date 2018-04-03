
## Pytest and Unittest

Originally the tests were written using` unittest.TestCase`.  Today tests are being written `pytest`.

Pytest makes it easier to write tests and provides better support for
controlling how long-lived fixtures are.  Some tests do focus on
startup behavior and need an entirely fresh environment.  However in a
lot of situations, significant time can be saved by not recreating
databases and SyncManagers for each test.

## Two Fixture approaches

The `utils.py` module includes a `SqlFixture` class that defines a
setUp and tearDown method to set up an entanglement environment.  This
is subclassed by `TestSql` and `GatewayTest` in `test_sql.py` and
`test_gateway.py`.  The TestSql subclass provides little additional
functionality.  The TestGateway class provides a third SyncManager for testing flooding behavior.

The `sql_fixture` pytest fixture permits a class approximately similar to `TestSql` to be used as a pytest fixture.

This unittest style fixture method proved to be hard to maintain.

There is a new method in `conftest.py` that only works for pytest tests.  Using modules may want to override `requested_layout` and almost certainly need to override the `registries` fixture.  The `registries` fixture returns a list of sql_sync_declarative_bases and registries to be attached to SyncServers.  Each registry is copied and a copy is attached to the SyncManagers.  The `requested_layout` fixture provides a dictionary describing which SyncManagers are needed in the test layout.  The default requests a `client` and `server` SyncManager providing a layout similar to the `SqlFixture` and `TestSql` classes.

A module can override the `layout` fixture and make it scoped to that module if the layout should persist across the entire module.

# Modules

This section attempts to give hints on where to put tests.

* test_entanglement.py: low-level tests that don't need any sql.  The
  fixture support in this file is even more primitive than the
  SqlFixture class.

* test_sql.py:  Tests that only need a server and a manager and that restart the entire environment on every test.

* test_gateway.py:  Tests that restart a three-manager environment on every test.
