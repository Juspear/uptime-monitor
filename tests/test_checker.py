import unittest

import httpx

from uptime_monitor.checker import check_site
from uptime_monitor.config import Site


def client_with(handler) -> httpx.AsyncClient:
    """An httpx client that never touches the network."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class CheckSiteTest(unittest.IsolatedAsyncioTestCase):
    async def test_up(self):
        async with client_with(lambda req: httpx.Response(200, text="hello")) as client:
            result = await check_site(client, Site(name="a", url="https://example.com"))
        self.assertTrue(result.ok)
        self.assertEqual(result.status, 200)
        self.assertIsNotNone(result.latency_ms)

    async def test_wrong_status(self):
        async with client_with(lambda req: httpx.Response(503)) as client:
            result = await check_site(client, Site(name="a", url="https://example.com"))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, 503)
        self.assertIn("503", result.error)

    async def test_custom_expected_status(self):
        async with client_with(lambda req: httpx.Response(204)) as client:
            site = Site(name="a", url="https://example.com", expected_status=204)
            result = await check_site(client, site)
        self.assertTrue(result.ok)

    async def test_keyword_missing(self):
        async with client_with(lambda req: httpx.Response(200, text="Maintenance")) as client:
            site = Site(name="a", url="https://example.com", keyword="Welcome")
            result = await check_site(client, site)
        self.assertFalse(result.ok)
        self.assertIn("Welcome", result.error)

    async def test_keyword_present(self):
        async with client_with(lambda req: httpx.Response(200, text="Welcome!")) as client:
            site = Site(name="a", url="https://example.com", keyword="Welcome")
            result = await check_site(client, site)
        self.assertTrue(result.ok)

    async def test_timeout(self):
        def handler(req):
            raise httpx.ReadTimeout("slow", request=req)

        async with client_with(handler) as client:
            result = await check_site(client, Site(name="a", url="https://example.com", timeout=3))
        self.assertFalse(result.ok)
        self.assertIn("timeout", result.error)

    async def test_connection_error(self):
        def handler(req):
            raise httpx.ConnectError("refused", request=req)

        async with client_with(handler) as client:
            result = await check_site(client, Site(name="a", url="https://example.com"))
        self.assertFalse(result.ok)
        self.assertIn("ConnectError", result.error)


if __name__ == "__main__":
    unittest.main()
