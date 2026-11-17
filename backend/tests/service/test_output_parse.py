"""output_parse helper 的行为测试：围栏剥离、JSON 提取与解析、文本清洗、DSML 出口。"""

import json

import pytest

from agent.prompts.output_parse import (
    DsmlError,
    clean_output_text,
    extract_json_object,
    parse_dsml,
    parse_json_output,
    strip_code_fence,
)


class TestStripCodeFence:
    def test_plain_json_untouched(self):
        assert strip_code_fence('{"a": 1}') == '{"a": 1}'

    def test_json_fence_stripped(self):
        text = '```json\n{"a": 1}\n```'
        assert strip_code_fence(text) == '{"a": 1}'

    def test_bare_fence_stripped(self):
        text = '```\n{"a": 1}\n```'
        assert strip_code_fence(text) == '{"a": 1}'

    def test_inner_content_kept(self):
        """剥离只作用于首尾围栏，正文里的嵌套围栏原样保留。"""
        text = "```text\n看这段：```json\n{x}\n```\n```"
        assert "```json" in strip_code_fence(text)


class TestExtractJsonObject:
    def test_direct_object(self):
        assert extract_json_object('{"a": 1}') == '{"a": 1}'

    def test_fenced_object(self):
        assert extract_json_object('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_object_with_surrounding_prose(self):
        """夹在说明文字里时取最外层对象。"""
        text = '好的，结果如下：\n{"a": 1}\n以上。'
        assert extract_json_object(text) == '{"a": 1}'

    def test_no_object_returns_stripped_text(self):
        """找不到对象时返回剥围栏后的原文，由调用方解析后报错。"""
        assert extract_json_object("这不是 JSON") == "这不是 JSON"


class TestParseJsonOutput:
    def test_fenced_json_parses(self):
        assert parse_json_output('```json\n{"a": 1}\n```') == {"a": 1}

    def test_invalid_json_raises_decode_error(self):
        with pytest.raises(json.JSONDecodeError):
            parse_json_output("{不是 JSON}")

    def test_non_object_text_raises_decode_error(self):
        with pytest.raises(json.JSONDecodeError):
            parse_json_output("纯文本回复")


class TestCleanOutputText:
    def test_private_dsml_tag_removed(self):
        assert clean_output_text("前 <|DSML|invoke|> 后") == "前  后"

    def test_zero_width_chars_removed(self):
        assert clean_output_text("a\u200bb\ufeffc\u2060d") == "abcd"

    def test_control_chars_removed(self):
        assert clean_output_text("a\x00b\x07c\x1fd") == "abcd"

    def test_normal_text_untouched(self):
        text = "正常中文与 English、换行\n保留"
        assert clean_output_text(text) == text


class TestDsmlReexport:
    """output_parse 是 AI 输出解析的统一出口；DSML 实现归 dsml.py，此处仅重导出。"""

    def test_parse_dsml_via_output_parse(self):
        text = (
            "<|DSML| calls>\n"
            '<|DSML| invoke name="foo">'
            '<|DSML| parameter name="x" string="true">你好</|DSML| parameter>'
            "</|DSML| invoke>\n"
            "</|DSML| calls>"
        )
        call = parse_dsml(text)
        assert call is not None and call.name == "foo"
        assert call.parameters == {"x": "你好"}

    def test_invalid_dsml_raises(self):
        with pytest.raises(DsmlError):
            parse_dsml("<|DSML|calls|>残缺")
