"""Tests for the continente package (connector + miniapp panel).

Conventions per tests/test_durable_events.py: DATA_DIR temp + pop
DATABASE_URI before agentboom_sdk.db is imported, per-class loop +
run_async, tearDownModule resetting db._op_lock._lock.

No network: every outbound call goes through the connector's module-level
http_get / http_post seams, which the tests monkeypatch with a canned
transport. The vault round-trip runs against a real temp SQLite database
with the vault package's own migration applied.
"""
import asyncio
import html as _html
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="agentboom-continente-tests-")
os.environ["DATA_DIR"] = str(pathlib.Path(_TMP) / "data")
os.environ.pop("DATABASE_URI", None)
os.environ["VAULT_KEY"] = "ab" * 32  # test key: 32 hex bytes

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG_PLATFORM = (REPO_ROOT / "src/agentboom/templates/connectors"
                / "continente/platform")
VAULT_MIGRATIONS = (REPO_ROOT / "src/agentboom/templates/packages"
                    / "vault/platform/migrations")

sys.path.insert(0, str(PKG_PLATFORM))

import connectors.continente as cont  # noqa: E402

# ── fixtures ──────────────────────────────────────────────────────────

#: Two real tiles (outer div.product[data-pid], inner product-tile repeats
#: the pid, impression JSON HTML-escaped) + one decoy div that carries a
#: data-pid but is not a product tile.
SEARCH_FRAGMENT = """
<div class="product product-tile--grid" data-pid="1001">
  <div class="product-tile" data-pid="1001"
       data-product-tile-impression='{&quot;name&quot;: &quot;Arroz Agulha Continente&quot;, &quot;id&quot;: 1001, &quot;price&quot;: 1.89, &quot;brand&quot;: &quot;Continente&quot;}'>
    <img class="product-tile__image"
         src="https://www.continente.pt/media/base/1001/arroz.jpg" alt="Arroz">
    <a class="product-tile__title"
       href="https://www.continente.pt/produto/arroz-agulha-continente-1001.html">
       Arroz Agulha Continente</a>
  </div>
</div>
<div class="quantity" data-pid="1001"></div>
<div class="product" data-pid="2002">
  <div class="product-tile" data-pid="2002"
       data-product-tile-impression='{&quot;name&quot;: &quot;Leite UHT Meio Gordo&quot;, &quot;id&quot;: 2002, &quot;price&quot;: &quot;2,10&quot;, &quot;brand&quot;: &quot;Meadalva&quot;}'>
    <a href="/produto/leite-uht-2002.html">Leite UHT Meio Gordo</a>
  </div>
</div>
"""

PDP_PAGE = """
<html><head>
<meta property="og:title" content="Arroz Agulha Continente 1kg | Continente Online">
<meta property="og:image" content="https://www.continente.pt/media/base/1001/arroz-lg.jpg">
<script type="application/ld+json">
{"@context":"http://schema.org","@type":"Product","name":"Arroz Agulha Continente 1kg",
 "image":["https://www.continente.pt/media/base/1001/arroz-lg.jpg"],
 "offers":{"@type":"Offer","price":"1.89","priceCurrency":"EUR",
           "priceValidUntil":"2026-09-30","availability":"http://schema.org/InStock"}}
</script>
</head><body></body></html>
"""

MORADAS_LOGGED_IN = (b'<html><div data-address-id="11111111-2222-3333-4444-555555555555">'
                     b'<p>home</p></div></html>')
MORADAS_ANONYMOUS = b"<html><body>iniciar sessao</body></html>"

ORDER_LIST_PAGE = """
<div class="ct-order-history--order-preview">
  <div class="ct-order-history--order-preview-delivery-date">17 Ago 25</div>
  <a href="/conta/detalhe-encomenda/?orderID=11111111-aaaa-bbbb-cccc-111111111111">ver</a>
</div>
<div class="ct-order-history--order-preview">
  <div class="ct-order-history--order-preview-delivery-date">3 Jun 25</div>
  <a href="/conta/detalhe-encomenda/?orderID=22222222-aaaa-bbbb-cccc-222222222222">ver</a>
</div>
"""

ORDER_1_LINES = [("1001", "Arroz Agulha Continente", 2, "3,78"),
                 ("2002", "Leite UHT Meio Gordo", 1, "2,10")]
ORDER_2_LINES = [("3003", "Ovos Brancos M", 12, "4,20")]


def _order_detail_page(lines, total=None):
    items = []
    for pid, name, qty, line_total in lines:
        items.append(
            f'<div class="ct-order-history--product-item" data-pid="{pid}" '
            f'data-category="Alimenta\xc3\xa7\xc3\xa3o">'
            f'<span class="ct-order-history--product-title">{name}</span>'
            f'<span class="ct-order-history--product-brand">Marca</span>'
            f'<span class="ct-order-history--product-quantity">{qty} un</span>'
            f'<span class="ct-order-history--product-total-price">{line_total}€</span>'
            f'</div>')
    total_html = (f'<div class="ct-order-history--order-total-price">'
                  f'{total}€</div>') if total else ""
    return ('<div class="ct-order-history--products-list">'
            + "".join(items) + total_html + "</div>")


ORDER_1_PAGE = _order_detail_page(ORDER_1_LINES, total="21,48")
ORDER_2_PAGE = _order_detail_page(ORDER_2_LINES, total="4,20")

CART_GET_JSON = {
    "numItems": 3,
    "items": [
        {"id": 1001, "productName": "Arroz Agulha Continente", "quantity": 2,
         "price": {"sales": {"value": 1.89, "currency": "EUR",
                             "formatted": "1,89€"},
                   "list": {"value": 2.10, "currency": "EUR",
                            "formatted": "2,10€"}},
         "priceTotal": {"value": 3.78, "currency": "EUR",
                        "formatted": "3,78€"}, "uuid": "u1"},
        {"id": 2002, "productName": "Leite UHT Meio Gordo", "quantity": 1,
         "price": {"sales": {"value": 2.10, "currency": "EUR",
                             "formatted": "2,10€"}},
         "priceTotal": 2.10, "uuid": "u2"},
    ],
    "totals": {"subTotal": "5,88€", "grandTotal": "5,88€",
               "grandTotalNumber": 5.88},
    "minimumOrderValueAmount": 20,
}
CART_ADD_OK_JSON = {"message": "Produto adicionado ao carrinho",
                    "isProductInStock": True, "error": None,
                    "quantityTotal": 3}


class FakeHttp:
    """Canned transport for the connector's http_get / http_post seams."""

    def __init__(self, responses=None):
        #: URL substring -> (status, body-bytes-or-jsonable)
        self.responses = responses or {}
        self.calls = []  # (method, url, kwargs)

    def _body(self, value):
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False).encode("utf-8")
        if isinstance(value, str):
            return value.encode("utf-8")
        return value

    def match(self, url):
        for key, resp in self.responses.items():
            if key in url:
                if isinstance(resp, tuple):
                    return resp[0], {}, self._body(resp[1])
                return resp, {}, self._body(b"")
        return 200, {}, b""

    async def http_get(self, url, headers=None, cookies=None):
        self.calls.append(("GET", url, {"headers": headers, "cookies": cookies}))
        return self.match(url)

    async def http_post(self, url, headers=None, data=None, cookies=None):
        self.calls.append(("POST", url, {"headers": headers, "data": data,
                                         "cookies": cookies}))
        return self.match(url)

    def install(self):
        self._old = (cont.http_get, cont.http_post)
        cont.http_get, cont.http_post = self.http_get, self.http_post
        return self

    def uninstall(self):
        cont.http_get, cont.http_post = self._old


class ContinenteTestCase(unittest.TestCase):
    """Base: per-class loop, vault migrations applied, seams unpatched after
    each test, probe cache reset after each test."""

    @classmethod
    def setUpClass(cls):
        from agentboom_sdk import db
        cls.db = db
        cls.loop = asyncio.new_event_loop()
        cls.loop.run_until_complete(db.run_migrations(VAULT_MIGRATIONS))

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(cls.db.close())
        cls.loop.close()
        # A contended asyncio.Lock binds to the loop that first waited on
        # it; give the next class (and the rest of the suite) a fresh one.
        cls.db._op_lock._lock = asyncio.Lock()

    def setUp(self):
        self.fake = None
        self.loop.run_until_complete(
            self.db.execute(
                "DELETE FROM vault_credentials WHERE service = ?",
                (cont.VAULT_SERVICE,)))
        self.loop.run_until_complete(
            self.db.execute(
                "DELETE FROM vault_audit WHERE service = ?",
                (cont.VAULT_SERVICE,)))
        cont._PROBE_CACHE.update(at=0.0, value=False, reason="")

    def tearDown(self):
        if self.fake is not None:
            self.fake.uninstall()
        cont._PROBE_CACHE.update(at=0.0, value=False, reason="")

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)


# ── pure parsers (no http, no db) ─────────────────────────────────────


class ParseCookieStringTests(unittest.TestCase):
    def test_pairs(self):
        self.assertEqual(cont.parse_cookie_string("a=1; b=2; c=3"),
                         {"a": "1", "b": "2", "c": "3"})

    def test_empty_and_blank(self):
        self.assertEqual(cont.parse_cookie_string(""), {})
        self.assertEqual(cont.parse_cookie_string("   ;  ; "), {})
        self.assertEqual(cont.parse_cookie_string(None), {})

    def test_equals_in_value(self):
        self.assertEqual(cont.parse_cookie_string("tok=a=b=c; x=y"),
                         {"tok": "a=b=c", "x": "y"})

    def test_whitespace(self):
        self.assertEqual(cont.parse_cookie_string("  a = b ; c=d  \n"),
                         {"a": "b", "c": "d"})

    def test_trailing_semicolon_and_empty_value(self):
        self.assertEqual(cont.parse_cookie_string("a=b;"), {"a": "b"})
        self.assertEqual(cont.parse_cookie_string("a=; b=c"), {"a": "", "b": "c"})

    def test_junk_without_equals_skipped(self):
        self.assertEqual(cont.parse_cookie_string("a=1; garbage; c=2"),
                         {"a": "1", "c": "2"})

    def test_unicode(self):
        self.assertEqual(
            cont.parse_cookie_string("s\xc3\xa3o=caf\xc3\xa9; \xc3\xa7\xc3\xa3o=na\xc3\xa7\xc3\xa3o"),
            {"s\xc3\xa3o": "caf\xc3\xa9", "\xc3\xa7\xc3\xa3o": "na\xc3\xa7\xc3\xa3o"})

    def test_real_shape(self):
        out = cont.parse_cookie_string(
            "sid=abc123; dwsid=def456; __cqact=1; cqcid=%3B%3B")
        self.assertEqual(out["sid"], "abc123")
        self.assertEqual(out["cqcid"], "%3B%3B")
        self.assertEqual(len(out), 4)


class TileAndOrderDateParserTests(unittest.TestCase):
    def test_parse_tiles_extracts_and_dedupes(self):
        results = cont.parse_tiles(SEARCH_FRAGMENT, limit=10)
        self.assertEqual(len(results), 2)
        first, second = results
        self.assertEqual(first["pid"], "1001")
        self.assertEqual(first["title"], "Arroz Agulha Continente")
        self.assertEqual(first["price"], 1.89)
        self.assertEqual(first["brand"], "Continente")
        self.assertEqual(first["url"],
                         "https://www.continente.pt/produto/"
                         "arroz-agulha-continente-1001.html")
        self.assertEqual(first["image"],
                         "https://www.continente.pt/media/base/1001/arroz.jpg")
        self.assertEqual(second["pid"], "2002")
        self.assertEqual(second["title"], "Leite UHT Meio Gordo")
        self.assertEqual(second["price"], 2.10)  # formatted "2,10"
        self.assertEqual(second["url"],
                         "https://www.continente.pt/produto/leite-uht-2002.html")
        self.assertIsNone(second["image"])

    def test_parse_tiles_limit(self):
        self.assertEqual(len(cont.parse_tiles(SEARCH_FRAGMENT, limit=1)), 1)

    def test_parse_tiles_empty_page(self):
        self.assertEqual(cont.parse_tiles("<html></html>"), [])

    def test_parse_order_date_pt_and_en(self):
        self.assertEqual(cont.parse_order_date("17 Ago 25"), "2025-08-17")
        self.assertEqual(cont.parse_order_date("3 Jun 25"), "2025-06-03")
        self.assertEqual(cont.parse_order_date("17 Aug 25"), "2025-08-17")
        self.assertEqual(cont.parse_order_date(" 01 Fev 26 "), "2026-02-01")
        self.assertIsNone(cont.parse_order_date("17 Augosto 25"))
        self.assertIsNone(cont.parse_order_date("31 Fev 25"))  # invalid date
        self.assertIsNone(cont.parse_order_date("garbage"))
        self.assertIsNone(cont.parse_order_date(None))


# ── Part 2: LoginProbeTests, VaultSessionTests, HttpApiTests, MiniappPanelTests ──


class LoginProbeTests(ContinenteTestCase):
    """is_logged_in: no vault needed when cookies are passed explicitly."""

    def test_no_cookies(self):
        ok, reason = self.run_async(cont.is_logged_in(cookies={}))
        self.assertFalse(ok)
        self.assertEqual(reason, "no session stored")

    def test_logged_in_probe(self):
        self.fake = FakeHttp({
            "/conta/moradas/": (200, MORADAS_LOGGED_IN),
        }).install()
        ok, reason = self.run_async(cont.is_logged_in(cookies={"sid": "x"}))
        self.assertTrue(ok)
        self.assertIn("saved-addresses", reason)

    def test_anonymous_probe(self):
        self.fake = FakeHttp({
            "/conta/moradas/": (200, MORADAS_ANONYMOUS),
        }).install()
        ok, reason = self.run_async(cont.is_logged_in(cookies={"sid": "x"}))
        self.assertFalse(ok)
        self.assertIn("not logged in", reason)

    def test_probe_http_error(self):
        self.fake = FakeHttp({
            "/conta/moradas/": (500, "server error"),
        }).install()
        ok, reason = self.run_async(cont.is_logged_in(cookies={"sid": "x"}))
        self.assertFalse(ok)
        self.assertEqual(reason, "probe failed (HTTP 500)")

    def test_cache_bypass(self):
        # First call: cache miss → HTTP probe.
        self.fake = FakeHttp({
            "/conta/moradas/": (200, MORADAS_LOGGED_IN),
        }).install()
        self.run_async(cont.is_logged_in(cookies={"sid": "x"}))
        probe_calls = sum(1 for c in self.fake.calls if c[0] == "GET" and "/conta/moradas/" in c[1])
        self.assertEqual(probe_calls, 1)
        # Second call within TTL: cache hit → no HTTP.
        self.run_async(cont.is_logged_in(cookies={"sid": "x"}))
        probe_calls_after = sum(1 for c in self.fake.calls if c[0] == "GET" and "/conta/moradas/" in c[1])
        self.assertEqual(probe_calls_after, 1, "cache hit must not fire another HTTP call")
        # force=True bypasses cache:
        self.run_async(cont.is_logged_in(cookies={"sid": "x"}, force=True))
        probe_calls_force = sum(1 for c in self.fake.calls if c[0] == "GET" and "/conta/moradas/" in c[1])
        self.assertEqual(probe_calls_force, 2, "force=True must bypass cache")


class VaultSessionTests(ContinenteTestCase):
    """Round-trip through the vault tables (AES-256-GCM, no HTTP)."""

    def test_set_get_roundtrip(self):
        cookies = {"sid": "abc123", "dwsid": "xyz", "pt_session": "caf\u00e9"}
        self.run_async(cont.set_cookies(cookies))
        jar = self.run_async(cont.get_cookies())
        self.assertEqual(jar, cookies)

    def test_overwrite(self):
        self.run_async(cont.set_cookies({"sid": "old"}))
        self.run_async(cont.set_cookies({"sid": "new"}))
        self.assertEqual(self.run_async(cont.get_cookies()), {"sid": "new"})

    def test_clear_roundtrip(self):
        self.assertTrue(self.run_async(cont.set_cookies({"sid": "x"})))
        self.assertTrue(self.run_async(cont.clear_session()))
        self.assertEqual(self.run_async(cont.get_cookies()), {})
        # Second clear on empty vault:
        self.assertFalse(self.run_async(cont.clear_session()))

    def test_set_empty_raises(self):
        with self.assertRaises(cont.ContinenteError):
            self.run_async(cont.set_cookies({}))

    def test_get_no_row(self):
        self.assertEqual(self.run_async(cont.get_cookies()), {})

    def test_no_vault_key(self):
        """Without VAULT_KEY, set raises ContinenteError and get returns {}."""
        vault_key = os.environ.pop("VAULT_KEY")
        try:
            with self.assertRaises(cont.ContinenteError):
                self.run_async(cont.set_cookies({"sid": "x"}))
            self.assertEqual(self.run_async(cont.get_cookies()), {})
        finally:
            os.environ["VAULT_KEY"] = vault_key

    def test_audit_store(self):
        """After set_cookies, vault_audit has a 'store' row."""
        self.run_async(cont.set_cookies({"sid": "audit-test"}))
        rows = self.loop.run_until_complete(
            self.db.fetchall(
                "SELECT service, action, detail FROM vault_audit WHERE service = ?",
                (cont.VAULT_SERVICE,)))
        self.assertTrue(any(r["action"] == "store" for r in rows))


class HttpApiTests(ContinenteTestCase):
    """Connector API exercised through FakeHttp seams. All HTTP is canned."""

    def test_search_exact_call(self):
        self.fake = FakeHttp({
            "Search-ShowAjax": (200, SEARCH_FRAGMENT),
        }).install()
        results = self.run_async(cont.search("gel de duche"))
        self.assertEqual(len(results), 2)
        # Verify exact URL
        search_call = [c for c in self.fake.calls if "Search-ShowAjax" in c[1]][0]
        expected_url = f"{cont.ACTION}/Search-ShowAjax?q=gel+de+duche"
        self.assertEqual(search_call[1], expected_url)
        # Verify X-Requested-With header at the seam
        self.assertIn("X-Requested-With", search_call[2]["headers"])
        self.assertEqual(search_call[2]["headers"]["X-Requested-With"], "XMLHttpRequest")

    def test_search_non_200(self):
        self.fake = FakeHttp({
            "Search-ShowAjax": (503, "service unavailable"),
        }).install()
        with self.assertRaises(cont.ContinenteError):
            self.run_async(cont.search("any"))

    def test_product_200(self):
        self.fake = FakeHttp({
            "/produto/p-1001": (200, PDP_PAGE),
        }).install()
        p = self.run_async(cont.product("1001"))
        self.assertIsNotNone(p)
        self.assertEqual(p["title"], "Arroz Agulha Continente 1kg")
        self.assertEqual(p["price"], 1.89)
        self.assertEqual(p["price_valid_until"], "2026-09-30")
        self.assertEqual(p["availability"], "InStock")
        self.assertTrue(p["in_stock"])

    def test_product_404(self):
        self.fake = FakeHttp({
            "/produto/p-9999": (404, ""),
        }).install()
        self.assertIsNone(self.run_async(cont.product("9999")))

    def test_cart_success(self):
        self.fake = FakeHttp({
            "Cart-Get": (200, CART_GET_JSON),
        }).install()
        state = self.run_async(cont.cart(cookies={"sid": "x"}))
        self.assertEqual(state["total_eur"], 5.88)
        self.assertEqual(state["num_items"], 3)
        self.assertEqual(len(state["items"]), 2)
        # Item 1 priced from {"sales":{"value":1.89}}
        self.assertEqual(state["items"][0]["price_eur"], 1.89)
        # Item 2 priced from plain number priceTotal
        self.assertEqual(state["items"][1]["price_eur"], 2.10)

    def test_cart_no_cookies(self):
        with self.assertRaises(cont.SessionError):
            self.run_async(cont.cart())

    def test_cart_401(self):
        self.fake = FakeHttp({
            "Cart-Get": (401, "unauthorized"),
        }).install()
        with self.assertRaises(cont.SessionError):
            self.run_async(cont.cart(cookies={"sid": "x"}))

    def test_cart_add_success(self):
        self.fake = FakeHttp({
            "/conta/moradas/": (200, MORADAS_LOGGED_IN),
            "Cart-AddProduct": (200, CART_ADD_OK_JSON),
            "Cart-Get": (200, CART_GET_JSON),
        }).install()
        result = self.run_async(cont.cart_add("1001", 2, cookies={"sid": "x"}))
        self.assertTrue(result["ok"])
        # Check POST data
        add_call = [c for c in self.fake.calls if "Cart-AddProduct" in c[1]][0]
        self.assertEqual(add_call[2]["data"], {"pid": "1001", "quantity": "2"})
        # cart_total_eur comes from Cart-Get mock
        self.assertEqual(result["cart_total_eur"], 5.88)

    def test_cart_add_anonymous_probe_fails(self):
        self.fake = FakeHttp({
            "/conta/moradas/": (200, MORADAS_ANONYMOUS),
        }).install()
        with self.assertRaises(cont.SessionError):
            self.run_async(cont.cart_add("1001", 2, cookies={"sid": "x"}))
        # No Cart-AddProduct call was made
        add_calls = [c for c in self.fake.calls if "Cart-AddProduct" in c[1]]
        self.assertEqual(len(add_calls), 0)

    def test_cart_add_no_cookies(self):
        with self.assertRaises(cont.SessionError):
            self.run_async(cont.cart_add("1001"))

    def test_cart_add_401(self):
        self.fake = FakeHttp({
            "/conta/moradas/": (200, MORADAS_LOGGED_IN),
            "Cart-AddProduct": (401, "unauthorized"),
        }).install()
        with self.assertRaises(cont.SessionError):
            self.run_async(cont.cart_add("1001", 2, cookies={"sid": "x"}))

    def test_cart_add_out_of_stock(self):
        oos_json = {"message": "Produto indispon\u00edvel", "isProductInStock": False, "error": True}
        self.fake = FakeHttp({
            "/conta/moradas/": (200, MORADAS_LOGGED_IN),
            "Cart-AddProduct": (200, oos_json),
        }).install()
        with self.assertRaises(cont.ContinenteError):
            self.run_async(cont.cart_add("1001", 2, cookies={"sid": "x"}))

    def test_orders_success(self):
        self.fake = FakeHttp({
            "/conta/encomendas/": (200, ORDER_LIST_PAGE),
            "orderID=11111111-aaaa-bbbb-cccc-111111111111": (200, ORDER_1_PAGE),
            "orderID=22222222-aaaa-bbbb-cccc-222222222222": (200, ORDER_2_PAGE),
        }).install()
        orders = self.run_async(cont.orders(cookies={"sid": "x"}))
        self.assertEqual(len(orders), 2)
        self.assertEqual(orders[0]["date"], "2025-08-17")
        self.assertEqual(orders[0]["total_eur"], 21.48)
        self.assertEqual(len(orders[0]["lines"]), 2)
        self.assertEqual(orders[1]["date"], "2025-06-03")
        self.assertEqual(orders[1]["total_eur"], 4.20)
        self.assertEqual(len(orders[1]["lines"]), 1)
        # Verify exactly 3 HTTP GETs: 1 list + 2 details
        self.assertEqual(len([c for c in self.fake.calls if c[0] == "GET"]), 3)

    def test_order_detail_no_cookies(self):
        with self.assertRaises(cont.SessionError):
            self.run_async(cont.order_detail("any-id"))

    def test_order_detail_404(self):
        self.fake = FakeHttp({
            "orderID=dead": (404, "not found"),
        }).install()
        # Store a session so get_cookies doesn't return {}
        self.run_async(cont.set_cookies({"sid": "x"}))
        with self.assertRaises(cont.SessionError):
            self.run_async(cont.order_detail("dead"))


class MiniappPanelTests(ContinenteTestCase):
    """Miniapp route handlers via direct calls (no TestClient — loop-bound locks)."""

    @classmethod
    def setUpClass(cls):
        # Import the miniapp without writing __pycache__ in the template tree.
        super().setUpClass()
        cls._old_dont_write = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location(
            "continente_miniapp",
            str(PKG_PLATFORM / "miniapps/continente/main.py"),
        )
        cls.miniapp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.miniapp)

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(cls.db.close())
        cls.loop.close()
        cls.db._op_lock._lock = asyncio.Lock()
        sys.dont_write_bytecode = cls._old_dont_write

    # ── helpers ─────────────────────────────────────────────────────

    def _res(self, resp):
        """FastAPI response → (status_code, dict-payload)."""
        from fastapi.responses import JSONResponse
        if isinstance(resp, JSONResponse):
            return resp.status_code, json.loads(resp.body)
        return 200, resp

    # ── tests ───────────────────────────────────────────────────────

    def test_router_shape(self):
        expected = {("GET", "/health"), ("GET", "/status"), ("POST", "/session"),
                    ("DELETE", "/session"), ("GET", "/search"),
                    ("POST", "/items/{pid}/add")}
        actual = set()
        for r in self.miniapp.router.routes:
            if r.path == "/":
                continue
            methods = getattr(r, "methods", set())
            for m in methods:
                actual.add((m, r.path))
        for method, path in expected:
            self.assertIn((method, path), actual,
                          f"Missing route: {method} {path} in {actual}")

    def test_health_missing(self):
        resp = self.run_async(self.miniapp.health())
        code, body = self._res(resp)
        self.assertEqual(code, 200)
        self.assertEqual(body["session"], "missing")
        self.assertIsNone(body["logged_in"])

    def test_status_missing_hint(self):
        resp = self.run_async(self.miniapp.status())
        code, body = self._res(resp)
        self.assertEqual(code, 200)
        self.assertIn("set up continente", body["hint"])

    def test_session_raw_string(self):
        self.fake = FakeHttp({
            "/conta/moradas/": (200, MORADAS_ANONYMOUS),
        }).install()
        resp = self.run_async(self.miniapp.store_session(
            {"cookies": "sid=abc; dwsid=xyz"}))
        code, body = self._res(resp)
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["cookie_count"], 2)

    def test_session_json_object(self):
        self.fake = FakeHttp({
            "/conta/moradas/": (200, MORADAS_ANONYMOUS),
        }).install()
        resp = self.run_async(self.miniapp.store_session(
            {"cookies": '{"sid": "abc", "dwsid": "xyz"}'}))
        code, body = self._res(resp)
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["cookie_count"], 2)

    def test_session_empty(self):
        resp = self.run_async(self.miniapp.store_session({"cookies": ""}))
        code, body = self._res(resp)
        self.assertEqual(code, 400)

    def test_session_blank(self):
        resp = self.run_async(self.miniapp.store_session({"cookies": "   "}))
        code, body = self._res(resp)
        self.assertEqual(code, 400)

    def test_secret_hygiene(self):
        """The cookie jar value must never appear in health/status responses."""
        self.run_async(cont.set_cookies({"sid": "SECRETVALUE123"}))
        self.fake = FakeHttp({
            "/conta/moradas/": (200, MORADAS_ANONYMOUS),
        }).install()
        h_resp = self.run_async(self.miniapp.health())
        s_resp = self.run_async(self.miniapp.status())
        h_body = json.dumps(self._res(h_resp)[1])
        s_body = json.dumps(self._res(s_resp)[1])
        self.assertNotIn("SECRETVALUE123", h_body)
        self.assertNotIn("SECRETVALUE123", s_body)

    def test_search_route(self):
        self.fake = FakeHttp({
            "Search-ShowAjax": (200, SEARCH_FRAGMENT),
        }).install()
        resp = self.run_async(self.miniapp.search_route(q="arroz"))
        code, body = self._res(resp)
        self.assertEqual(code, 200)
        self.assertEqual(body["count"], 2)
        # Missing q → 400
        resp2 = self.run_async(self.miniapp.search_route(q=""))
        code2, body2 = self._res(resp2)
        self.assertEqual(code2, 400)

    def test_add_item_no_session(self):
        resp = self.run_async(self.miniapp.add_item("1001", {"quantity": 1}))
        code, body = self._res(resp)
        self.assertEqual(code, 503)
        self.assertIn("set up continente", body["error"])

    def test_add_item_success(self):
        # Store a session so get_cookies returns it
        self.run_async(cont.set_cookies({"sid": "x"}))
        self.fake = FakeHttp({
            "/conta/moradas/": (200, MORADAS_LOGGED_IN),
            "Cart-AddProduct": (200, CART_ADD_OK_JSON),
            "Cart-Get": (200, CART_GET_JSON),
        }).install()
        resp = self.run_async(self.miniapp.add_item("1001", {"quantity": 2}))
        code, body = self._res(resp)
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

    def test_delete_session(self):
        self.run_async(cont.set_cookies({"sid": "x"}))
        resp = self.run_async(self.miniapp.delete_session())
        code, body = self._res(resp)
        self.assertTrue(body["deleted"])
        # Health now shows "missing"
        h_resp = self.run_async(self.miniapp.health())
        h_code, h_body = self._res(h_resp)
        self.assertEqual(h_body["session"], "missing")


def tearDownModule():
    from agentboom_sdk import db
    db._op_lock._lock = asyncio.Lock()


if __name__ == "__main__":
    unittest.main()
