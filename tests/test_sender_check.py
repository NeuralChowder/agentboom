"""Tests for agentboom_sdk.sender_check — pure functions, no IO."""
import unittest

from agentboom_sdk import sender_check as sc


class ParseAuthResultsTests(unittest.TestCase):
    def test_single_line(self):
        out = sc.parse_auth_results(
            "mx.example.com; dkim=pass spf=pass dmarc=pass")
        self.assertEqual(out["dkim"], ["pass"])
        self.assertEqual(out["spf"], ["pass"])
        self.assertEqual(out["dmarc"], ["pass"])

    def test_multiple_hops_keep_order(self):
        out = sc.parse_auth_results(
            "relay.example.com; spf=none dkim=none",
            "mx.example.com; spf=pass dkim=pass dmarc=pass")
        self.assertEqual(out["spf"], ["none", "pass"])

    def test_absent_mechanisms_stay_empty(self):
        out = sc.parse_auth_results("mx.example.com; spf=pass")
        self.assertEqual(out["dkim"], [])
        self.assertIsNone(out.get("nothing"))
        self.assertEqual(set(out), {"spf", "dkim", "dmarc"})

    def test_none_values_ignored(self):
        self.assertEqual(sc.parse_auth_results(None, None),
                         {"spf": [], "dkim": [], "dmarc": []})


class AuthStatusTests(unittest.TestCase):
    def test_no_data_unverified(self):
        self.assertEqual(sc.auth_status(None), "unverified")
        self.assertEqual(sc.auth_status({}), "unverified")

    def test_any_fail_is_fail(self):
        self.assertEqual(
            sc.auth_status({"spf": ["none"], "dkim": ["fail"],
                            "dmarc": ["none"]}), "fail")

    def test_dmarc_pass_is_pass(self):
        self.assertEqual(
            sc.auth_status({"spf": ["softfail"], "dkim": ["pass"],
                            "dmarc": ["pass"]}), "pass")

    def test_first_spf_and_dkim_pass_is_pass(self):
        self.assertEqual(
            sc.auth_status({"spf": ["pass"], "dkim": ["pass"]}), "pass")

    def test_spf_alone_is_unverified(self):
        self.assertEqual(sc.auth_status({"spf": ["pass"]}), "unverified")

    def test_later_hop_pass_does_not_count(self):
        # The first recorded line is the receiving MX's; a downstream hop
        # passing must not upgrade an unverified first hop.
        self.assertEqual(
            sc.auth_status({"spf": ["none", "pass"], "dkim": ["none", "pass"]}),
            "unverified")


class DomainTests(unittest.TestCase):
    def test_domain_of(self):
        self.assertEqual(sc.domain_of("Bank <noreply@bank.example>"),
                         "bank.example")
        self.assertEqual(sc.domain_of("noreply@bank.example"), "bank.example")
        self.assertEqual(sc.domain_of(None), "")

    def test_lookalike_single_char(self):
        self.assertTrue(sc.is_lookalike_domain("example-bank.0pt",
                                               "example-bank.com"))

    def test_lookalike_tld_swap(self):
        self.assertTrue(sc.is_lookalike_domain("company.com", "company.pt"))

    def test_identical_not_lookalike(self):
        self.assertFalse(sc.is_lookalike_domain("bank.example",
                                                "bank.example"))

    def test_unrelated_not_lookalike(self):
        self.assertFalse(sc.is_lookalike_domain("other.example",
                                                "bank.example"))


class VerdictTests(unittest.TestCase):
    def test_exact_match_with_pass(self):
        v = sc.verdict(expected_sender="noreply@bank.example",
                       from_header="Bank <noreply@bank.example>",
                       from_email="noreply@bank.example",
                       auth={"spf": ["pass"], "dkim": ["pass"],
                             "dmarc": ["pass"], "status": "pass"})
        self.assertEqual(v["status"], "ok")

    def test_mismatch(self):
        v = sc.verdict(expected_sender="noreply@bank.example",
                       from_header="Bank <noreply@evil.example>",
                       from_email="noreply@evil.example", auth=None)
        self.assertEqual(v["status"], "mismatch")

    def test_auth_fail_is_suspicious(self):
        v = sc.verdict(expected_sender="noreply@bank.example",
                       from_header="Bank <noreply@bank.example>",
                       from_email="noreply@bank.example",
                       auth={"spf": ["fail"], "dkim": ["pass"],
                             "dmarc": ["fail"], "status": "fail"})
        self.assertEqual(v["status"], "suspicious")

    def test_no_evidence_is_unverified(self):
        v = sc.verdict(expected_sender="noreply@bank.example",
                       from_header="Bank <noreply@bank.example>",
                       from_email="noreply@bank.example", auth=None)
        self.assertEqual(v["status"], "unverified")

    def test_return_path_mismatch_downgrades(self):
        v = sc.verdict(
            expected_sender="noreply@bank.example",
            from_header="Bank <noreply@bank.example>",
            from_email="noreply@bank.example",
            auth={"spf": ["pass"], "dkim": ["pass"], "dmarc": ["pass"],
                  "status": "pass", "return_path_domain": "relay.example",
                  "from_return_path_mismatch": True})
        self.assertEqual(v["status"], "unverified")


if __name__ == "__main__":
    unittest.main()
