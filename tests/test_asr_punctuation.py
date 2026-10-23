import pytest

from proximic_ring.asr.punctuation import trim_short_utterance_end


@pytest.mark.parametrize("text, expected", [
    ("好的。", "好的"),
    ("真的吗？！", "真的吗"),
    ("好，明天见。", "好，明天见"),
    ("好的。明天见！", "好的。明天见"),
    ("等一下……", "等一下"),
    ("你好，", "你好"),
    ("你好\n明天见。", "你好\n明天见"),
    ("Hello, world!", "Hello, world"),
    ("Don't change C++ or node.js.", "Don't change C++ or node.js"),
    ("价格是3.14元。", "价格是3.14元"),
    ("3.14", "3.14"),
    ("版本1.2.3。", "版本1.2.3"),
    ("https://example.com/a.b?q=1.2。", "https://example.com/a.b?q=1.2"),
    ("https://example.com/search?", "https://example.com/search?"),
    ("打开https://example.com/path!。", "打开https://example.com/path!"),
    ("Use U.S.", "Use U.S."),
    ("给我“你好”。", "给我“你好”"),
    ("他说“你好。”", "他说“你好。”"),
    ("写成（好的）。", "写成（好的）"),
    ("C++", "C++"),
    ("你好😊", "你好😊"),
    ("。！？", "。！？"),
    ("", ""),
])
def test_short_final_only_trims_sentence_end(text, expected):
    assert trim_short_utterance_end(text) == expected


@pytest.mark.parametrize("unit, separator", [
    ("字", ""), ("hello", " "), ("don't", " "), ("self-hosted", " "), ("3.14", " "),
])
def test_twenty_units_are_short_but_twenty_one_are_preserved(unit, separator):
    short = separator.join([unit] * 20)
    long = separator.join([unit] * 21)
    assert trim_short_utterance_end(short + "。") == short
    assert trim_short_utterance_end(long + "。") == long + "。"


def test_mixed_language_counts_words_not_letters_and_ignores_spaces_and_punctuation():
    short = "字" * 18 + ", hello world"
    assert trim_short_utterance_end(short + "。") == short
    assert trim_short_utterance_end(short + "再。") == short + "再。"


def test_length_applies_to_whole_utterance_not_each_sentence():
    text = "字" * 11 + "。" + "字" * 10 + "。"
    assert trim_short_utterance_end(text) == text
