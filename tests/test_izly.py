import unittest

from crousandgo.errors import ShopError
from crousandgo.izly import order_pay_fields, pay_pending_order

PAGE = """
<form id="order_review" method="post">
  <input type="hidden" name="woocommerce_pay" value="1">
  <input type="hidden" name="woocommerce-pay-nonce" value="abc123">
  <input type="radio" name="payment_method" value="izlyvl" checked>
  <input type="radio" name="payment_method" value="other">
  <input type="checkbox" name="terms-optional" value="x">
  <button type="submit" id="place_order" value="Payer la commande">Payer la commande</button>
</form>
"""


class FakeResponse:
    def __init__(self, text="", status_code=200, location=None):
        self.text = text
        self.status_code = status_code
        self.headers = {"location": location} if location else {}


class FakeHttp:
    """Stands in for httpx.Client: records the POST, never touches the network."""

    def __init__(self, post_response):
        self.post_response = post_response
        self.posted = None

    def get(self, url):
        return FakeResponse(PAGE)

    def post(self, url, data=None, follow_redirects=False):
        self.posted = (url, data)
        return self.post_response


class OrderPayTests(unittest.TestCase):
    def test_fields_keep_hidden_and_checked_only(self):
        f = order_pay_fields(PAGE)
        self.assertEqual(f["woocommerce-pay-nonce"], "abc123")
        self.assertEqual(f["payment_method"], "izlyvl")
        self.assertNotIn("terms-optional", f)
        self.assertEqual(f["place_order"], "Payer la commande")

    def test_missing_form_is_a_shop_error(self):
        with self.assertRaises(ShopError):
            order_pay_fields("<html>no form</html>")

    def test_returns_izly_url_without_paying(self):
        url = "https://rest.izly.fr/api/Izly/WebPayments/1/Authorize?token=x"
        http = FakeHttp(FakeResponse(status_code=302, location=url))
        self.assertEqual(pay_pending_order(http, "https://shop/order-pay/1/"), url)
        self.assertEqual(http.posted[1]["payment_method"], "izlyvl")

    def test_non_izly_redirect_is_refused(self):
        http = FakeHttp(FakeResponse(status_code=302, location="https://shop/fail"))
        with self.assertRaises(ShopError):
            pay_pending_order(http, "https://shop/order-pay/1/")


if __name__ == "__main__":
    unittest.main()
