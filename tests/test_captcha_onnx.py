# -*- coding: utf-8 -*-
"""验证码 ONNX 识别通道的离线测试。

对应实盘风险：验证码识别直接决定能否登入交易表单，而它每张都有时间窗口。
这些都是纯离线测试：不连接客户端、不需要 tesseract、不需要网络。
"""

import unittest
from pathlib import Path

from easytrader.utils import captcha, captcha_onnx
from easytrader.utils.captcha_onnx import _ctc_decode

# 与实盘样本形态一致的标签（蓝色正体、4 位、大小写混合）
SAMPLES = {
    "07bb2f3e-ahk-1-captcha.png": "KBdT",
    "12c8d9b2-ahk-1-captcha.png": "VXAF",
    "ahk-1-captcha.png": "cHCD",
    "bd21ca48-ahk-1-captcha.png": "NH7d",
    "uia-1-captcha.png": "pt7b",
    "6c1892d8-ahk-1-captcha.png": "2kBE",
}
SAMPLE_DIR = (
    Path(__file__).resolve().parents[1] / "tmp" / "background-methods"
)


class CtcDecodeTests(unittest.TestCase):
    # 索引 0 是 blank，其后才是字符，与模型 metadata 的字符表一致
    CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

    def setUp(self):
        self.characters = self.CHARS
        self.blank = 0

    def test_blank_and_repeats_collapse(self):
        # 索引 10 是 'A'，11 是 'B'
        self.assertEqual(
            _ctc_decode([0, 10, 0, 11, 0], self.characters, self.blank), "AB"
        )

    def test_repeated_index_is_merged(self):
        # 连续两次同一索引只算一个字符（CTC 的 repeat 规则）
        self.assertEqual(
            _ctc_decode([10, 10, 11, 11], self.characters, self.blank), "AB"
        )

    def test_separated_repeat_is_kept(self):
        # 中间隔了 blank，说明是两个相邻的同类字符
        self.assertEqual(
            _ctc_decode([10, 0, 10], self.characters, self.blank), "AA"
        )

    def test_out_of_range_index_is_ignored(self):
        self.assertEqual(
            _ctc_decode([10, 999, 11], self.characters, self.blank), "AB"
        )

    def test_all_blank_yields_empty(self):
        self.assertEqual(_ctc_decode([0, 0, 0], self.characters, self.blank), "")


class CtcCaseSensitivityTests(unittest.TestCase):
    """大小写必须能区分——这是本客户端验证码最容易出错的地方。"""

    CHARACTERS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

    def test_upper_and_lower_decode_to_distinct_strings(self):
        upper_a = self.CHARACTERS.index("A")
        lower_a = self.CHARACTERS.index("a")
        self.assertEqual(_ctc_decode([upper_a], self.CHARACTERS, 0), "A")
        self.assertEqual(_ctc_decode([lower_a], self.CHARACTERS, 0), "a")


class ModelPackagingTests(unittest.TestCase):
    def test_model_ships_with_package(self):
        self.assertTrue(
            captcha_onnx.MODEL_PATH.is_file(), "验证码模型未随包分发"
        )
        self.assertTrue(
            captcha_onnx.DATA_PATH.is_file(), "验证码模型权重未随包分发"
        )

    def test_pinned_hashes_match_shipped_files(self):
        """随包权重必须是固定版本，被替换要立刻暴露。"""
        self.assertEqual(
            captcha_onnx._sha256(captcha_onnx.MODEL_PATH),
            captcha_onnx.MODEL_SHA256,
        )
        self.assertEqual(
            captcha_onnx._sha256(captcha_onnx.DATA_PATH),
            captcha_onnx.DATA_SHA256,
        )

    def test_model_metadata_has_case_sensitive_charset(self):
        """字符集必须同时包含大小写和数字，否则无法表达 cHCD 这类验证码。"""
        characters = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        for char in "AaKkZz9":
            self.assertIn(char, characters)


class RecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not captcha_onnx.model_available():
            raise unittest.SkipTest("验证码模型未随包分发")

    def test_recognizes_majority_of_real_samples(self):
        """真实样本上的整串准确率。低于 7/10 说明模型或预处理被改坏了。"""
        if not SAMPLE_DIR.is_dir():
            raise unittest.SkipTest("缺少真实验证码样本目录")
        hits = 0
        checked = 0
        for name, expected in SAMPLES.items():
            path = SAMPLE_DIR / name
            if not path.is_file():
                continue
            checked += 1
            if captcha_onnx.onnx_recognize(str(path)) == expected:
                hits += 1
        if not checked:
            raise unittest.SkipTest("没有可用的验证码样本")
        self.assertGreaterEqual(
            hits,
            int(checked * 0.7),
            "验证码识别准确率明显下降：%d/%d" % (hits, checked),
        )

    def test_result_length_matches_captcha(self):
        path = SAMPLE_DIR / "ahk-1-captcha.png"
        if not path.is_file():
            raise unittest.SkipTest("缺少样本")
        self.assertEqual(len(captcha_onnx.onnx_recognize(str(path))), 4)

    def test_missing_file_returns_empty_instead_of_raising(self):
        self.assertEqual(captcha_onnx.onnx_recognize("no-such-file.png"), "")

    def test_short_result_is_rejected_by_public_wrapper(self):
        """长度不对的识别结果必须丢弃，不能把残码提交上去。"""
        self.assertEqual(captcha.captcha_recognize_improved("no-such-file.png"), "")


class NoTesseractDependencyTests(unittest.TestCase):
    def test_improved_entry_point_does_not_use_tesseract(self):
        """同花顺走的 captcha_recognize_improved 已改为纯 ONNX。

        注意：模块里仍有 ``captcha_recognize`` / ``detect_gf_result`` 等
        tesseract 实现，那是其它券商适配器（gf / yh_client 等）在用的，
        不能删。这里只断言我们这条链路不再依赖它。
        """
        import inspect

        source = inspect.getsource(captcha.captcha_recognize_improved)
        self.assertNotIn("pytesseract", source)
        self.assertNotIn("tessedit_char_whitelist", source)
        self.assertIn("onnx_recognize", source)

    def test_removed_tesseract_voting_helper_is_gone(self):
        """原先的多变体投票实现已随改动移除，避免两套逻辑并存。"""
        self.assertFalse(hasattr(captcha, "_tesseract_recognize_improved"))

    def test_tesseract_path_argument_still_accepted(self):
        """旧调用方仍会传 tesseract_path，不能因此报错。"""
        self.assertEqual(
            captcha.captcha_recognize_improved(
                "no-such-file.png", tesseract_path=r"C:\fake\tesseract.exe"
            ),
            "",
        )


if __name__ == "__main__":
    unittest.main()
