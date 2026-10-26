# coding: utf-8
import unittest

from easytrader.utils.captcha import (
    normalize_captcha_result,
    select_captcha_candidate,
)
from easytrader.xqtrader import XueQiuTrader


class TestXueQiuTrader(unittest.TestCase):
    def test_prepare_account(self):
        user = XueQiuTrader()
        params_without_cookies = dict(
            portfolio_code="ZH123456", portfolio_market="cn"
        )
        with self.assertRaises(TypeError):
            user._prepare_account(**params_without_cookies)

        params_without_cookies.update(cookies="123")
        user._prepare_account(**params_without_cookies)
        self.assertEqual(params_without_cookies, user.account_config)


class TestCaptchaHelpers(unittest.TestCase):
    def test_normalizes_valid_four_character_code(self):
        self.assertEqual(normalize_captcha_result("A-B 1/2"), "AB12")

    def test_rejects_wrong_length(self):
        self.assertEqual(normalize_captcha_result("abc"), "")

    def test_selects_unique_majority(self):
        self.assertEqual(
            select_captcha_candidate(["ab12", "cd34", "ab12"]), "ab12"
        )

    def test_rejects_tied_candidates(self):
        self.assertEqual(select_captcha_candidate(["ab12", "cd34"]), "")
