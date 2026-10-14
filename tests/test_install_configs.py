from __future__ import annotations

import json
import tomllib

import pytest

from codalith.install.configs import merge_server


def merge(text: str, *, format: str = "json", replace: bool = False) -> str:
    return merge_server(
        text,
        format=format,
        server_path=("mcp_servers",) if format == "toml" else ("mcpServers",),
        name="codalith",
        entry={"url": "http://127.0.0.1:8765/mcp"},
        replace=replace,
    )


@pytest.mark.parametrize("text", ["{}", "{\n}\n", "{\r\n}\r\n", '{"theme": "dark"}'])
def test_json_adds_only_the_requested_server(text: str) -> None:
    result = merge(text)
    expected = json.loads(text)
    expected["mcpServers"] = {"codalith": {"url": "http://127.0.0.1:8765/mcp"}}
    assert json.loads(result) == expected
    assert merge(result) == result
    if "\r\n" in text:
        assert "\n" not in result.replace("\r\n", "")


def test_json_keeps_unrelated_values_and_whitespace_byte_identical() -> None:
    text = '{\n\t"mcpServers": {"other": {"command":"keep", "args":["x"]}},\n  "secret": "never-print-me"\n}\n'
    result = merge(text)
    assert '"other": {"command":"keep", "args":["x"]}' in result
    assert '\n  "secret": "never-print-me"\n}\n' in result
    assert json.loads(result)["mcpServers"]["other"] == {"command": "keep", "args": ["x"]}


@pytest.mark.parametrize(
    "text",
    [
        '{ // leading\n "mcpServers": {\n // registered\n }, // trailing\n}',
        '{"mcpServers": {"other": {"url": "http://else/"}, /* keep */},}',
        '{"mcpServers": {"other": {"url": "http://else/"} // keep\n},}',
        '{"mcpServers": { /* empty */ }}',
        '{"array": [1,2,], "mcpServers": {},}',
    ],
)
def test_jsonc_preserves_comments_trailing_commas_and_is_idempotent(text: str) -> None:
    result = merge(text, format="jsonc")
    assert merge(result, format="jsonc") == result
    for comment in (
        "// leading",
        "// registered",
        "// trailing",
        "/* keep */",
        "// keep",
        "/* empty */",
    ):
        if comment in text:
            assert comment in result
    assert "http://else/" in result if "http://else/" in text else True


def test_jsonc_does_not_treat_string_content_as_comments() -> None:
    text = '{"note": "https://host/path/*literal*/\\"//tail", "mcpServers": {}}'
    result = merge(text, format="jsonc")
    assert json.loads(result)["note"] == json.loads(text)["note"]
    assert '"note": "https://host/path/*literal*/\\"//tail"' in result


@pytest.mark.parametrize("format", ["json", "jsonc", "toml"])
def test_existing_conflict_requires_explicit_replacement(format: str) -> None:
    text = (
        '[mcp_servers.codalith]\nurl = "private-existing-value"\n'
        if format == "toml"
        else '{"mcpServers":{"codalith":{"url":"private-existing-value"}}}'
    )
    with pytest.raises(ValueError) as error:
        merge(text, format=format)
    assert "private-existing-value" not in str(error.value)
    result = merge(text, format=format, replace=True)
    assert "private-existing-value" not in result
    assert merge(result, format=format) == result


@pytest.mark.parametrize(
    ("format", "text"),
    [
        ("json", '{"mcpServers": {}, "mcpServers": {}}'),
        ("jsonc", '{/* c */"mcpServers": {}, "mcpServers": {}}'),
        ("json", '{"other":{"nested":1,"nested":2}}'),
        ("json", '{"mcpServers": {},}'),
        ("json", '{// comment\n"mcpServers": {}}'),
        ("jsonc", '{"mcpServers": {,}}'),
        ("jsonc", '{"mcpServers": {"x": 1 "y": 2}}'),
        ("jsonc", '{"note": "unterminated}'),
        ("jsonc", "{/* unfinished"),
        ("json", "[]"),
        ("json", '{"secret":NaN}'),
        ("toml", 'model = "one"\nmodel = "two"\n'),
        ("toml", '[mcp_servers.codalith]\nurl = "one"\nurl = "two"\n'),
    ],
)
def test_invalid_or_ambiguous_configuration_is_rejected(format: str, text: str) -> None:
    with pytest.raises(ValueError):
        merge(text, format=format)


@pytest.mark.parametrize("format", ["json", "jsonc", "toml"])
def test_wrong_parent_type_is_not_overwritten(format: str) -> None:
    text = (
        'mcp_servers = "private-parent-value"\n'
        if format == "toml"
        else '{"mcpServers":"private-parent-value"}'
    )
    with pytest.raises(ValueError) as error:
        merge(text, format=format, replace=True)
    assert "private-parent-value" not in str(error.value)


def test_nested_json_server_path_and_escaped_name() -> None:
    text = '{"mcp": {"other": true}, "keep": "yes"}'
    entry: dict[str, object] = {"url": 'https://local/中文?q=\\"', "args": ["a\\b", "line\nnext"]}
    result = merge_server(
        text, format="json", server_path=("mcp", "servers"), name='co"dalith', entry=entry
    )
    assert json.loads(result) == {
        "mcp": {"other": True, "servers": {'co"dalith': entry}},
        "keep": "yes",
    }


@pytest.mark.parametrize("text", ["", '# keep\nmodel = "other"\n', 'model = "other"\r\n'])
def test_toml_append_preserves_existing_bytes(text: str) -> None:
    result = merge(text, format="toml")
    assert result.startswith(text)
    assert tomllib.loads(result)["mcp_servers"]["codalith"] == {"url": "http://127.0.0.1:8765/mcp"}
    assert merge(result, format="toml") == result
    if "\r\n" in text:
        assert "\n" not in result.replace("\r\n", "")


def test_toml_replace_changes_only_target_and_descendant_tables() -> None:
    text = '# private model\nmodel = "keep"\n\n[mcp_servers.other]\ncommand = "stay" # keep inline\n\n[mcp_servers.codalith]\nurl = "old"\n\n[mcp_servers.codalith.headers]\nAuthorization = "old-private-key"\n\n[profiles.fast]\nmodel = "unchanged"\n'
    result = merge(text, format="toml", replace=True)
    assert result.startswith(
        '# private model\nmodel = "keep"\n\n[mcp_servers.other]\ncommand = "stay" # keep inline\n\n'
    )
    assert result.endswith('[profiles.fast]\nmodel = "unchanged"\n')
    assert "old-private-key" not in result
    expected = tomllib.loads(text)
    expected["mcp_servers"]["codalith"] = {"url": "http://127.0.0.1:8765/mcp"}
    assert tomllib.loads(result) == expected


def test_toml_header_like_lines_in_multiline_strings_are_not_edited() -> None:
    text = 'prompt = """\n[mcp_servers.codalith]\nurl = "fake"\n"""\n\n[mcp_servers.codalith]\nurl = "real-old"\n\n[profiles.other]\nmodel = "keep"\n'
    result = merge(text, format="toml", replace=True)
    assert result.startswith('prompt = """\n[mcp_servers.codalith]\nurl = "fake"\n"""\n\n')
    assert tomllib.loads(result)["prompt"] == tomllib.loads(text)["prompt"]


@pytest.mark.parametrize("delimiter", ['"', "'"])
@pytest.mark.parametrize("closing_count", [4, 5])
def test_toml_multiline_strings_ending_with_quote_content_are_preserved(
    delimiter: str, closing_count: int
) -> None:
    text = f"prompt = {delimiter * 3}hello{delimiter * closing_count}\n\n[mcp_servers.codalith]\nurl = 'old'\n"
    result = merge(text, format="toml", replace=True)
    assert tomllib.loads(result)["prompt"] == tomllib.loads(text)["prompt"]


def test_toml_keeps_unrelated_nan_and_inter_table_comments() -> None:
    text = 'metric = nan\n[mcp_servers.codalith]\nurl = "old"\n\n# preserve profile note\n[profiles.fast]\nmodel = "keep"\n'
    result = merge(text, format="toml", replace=True)
    assert result.startswith("metric = nan\n")
    assert '\n# preserve profile note\n[profiles.fast]\nmodel = "keep"\n' in result


@pytest.mark.parametrize("format", ["json", "jsonc", "toml"])
def test_desired_subset_preserves_extra_user_server_settings_verbatim(format: str) -> None:
    text = (
        '[mcp_servers.codalith]\nurl = "http://127.0.0.1:8765/mcp"\nheaders = { Authorization = "private-user-token" }\nenabled_tools = ["read"]\n'
        if format == "toml"
        else '{"mcpServers": {"codalith": {"url": "http://127.0.0.1:8765/mcp", "headers": {"Authorization":"private-user-token"}, "enabled_tools":["read"]}}}'
    )
    assert merge(text, format=format) == text
    assert merge(text, format=format, replace=True) == text


def test_nested_desired_subset_keeps_existing_env_settings() -> None:
    text = '{"mcpServers":{"codalith":{"env":{"MODE":"read", "TOKEN":"private-user-token"}}}}'
    assert (
        merge_server(
            text,
            format="json",
            server_path=("mcpServers",),
            name="codalith",
            entry={"env": {"MODE": "read"}},
        )
        == text
    )
    with pytest.raises(ValueError) as error:
        merge_server(
            text,
            format="json",
            server_path=("mcpServers",),
            name="codalith",
            entry={"env": {"MODE": "different"}},
        )
    assert "private-user-token" not in str(error.value)


@pytest.mark.parametrize(
    "extra",
    [
        {"enabled": False},
        {"disabled": True},
        {"command": "private-legacy-command"},
        {"args": ["private-legacy-arg"]},
        {"type": "stdio"},
        {"type": "sse"},
        {"transport": "sse"},
    ],
)
@pytest.mark.parametrize("format", ["json", "jsonc", "toml"])
def test_disabled_or_conflicting_transport_cannot_count_as_installed(
    extra: dict[str, object], format: str
) -> None:
    existing = {
        "url": "http://127.0.0.1:8765/mcp",
        "headers": {"Authorization": "private-user-token"},
        **extra,
    }
    if format == "toml":
        text = merge_server(
            "", format=format, server_path=("mcp_servers",), name="codalith", entry=existing
        )
    else:
        text = json.dumps({"mcpServers": {"codalith": existing}})
    with pytest.raises(ValueError) as error:
        merge(text, format=format)
    assert "private" not in str(error.value)
    result = merge(text, format=format, replace=True)
    decoded = tomllib.loads(result) if format == "toml" else json.loads(result)
    parent = "mcp_servers" if format == "toml" else "mcpServers"
    assert decoded[parent]["codalith"] == {"url": "http://127.0.0.1:8765/mcp"}


def test_toml_quoted_keys_and_inline_entry_values_round_trip() -> None:
    entry: dict[str, object] = {
        "url": "https://local/中文",
        "env": {"A KEY": 'a\\b"c'},
        "enabled": True,
        "args": ["--one", "\t"],
        "timeout": 2.5,
    }
    result = merge_server(
        "# keep\n", format="toml", server_path=("mcp servers",), name='co"dalith', entry=entry
    )
    assert tomllib.loads(result) == {"mcp servers": {'co"dalith': entry}}


@pytest.mark.parametrize(
    "text", ['mcp_servers.codalith.url = "old"\n', 'mcp_servers = { codalith = { url = "old" } }\n']
)
def test_toml_unsafe_inline_or_dotted_replacement_is_explicitly_rejected(text: str) -> None:
    with pytest.raises(ValueError, match="inline/dotted"):
        merge(text, format="toml", replace=True)


def test_toml_semantically_identical_inline_entry_is_left_untouched() -> None:
    text = 'mcp_servers = { codalith = { url = "http://127.0.0.1:8765/mcp" } } # keep\n'
    assert merge(text, format="toml") == text


def test_toml_refuses_appending_into_a_sealed_inline_parent() -> None:
    with pytest.raises(ValueError):
        merge('mcp_servers = { other = { url = "keep" } }\n', format="toml")


def test_replacement_distinguishes_boolean_from_numeric_values() -> None:
    text = '{"mcpServers":{"codalith":{"enabled":1}}}'
    with pytest.raises(ValueError):
        merge_server(
            text,
            format="json",
            server_path=("mcpServers",),
            name="codalith",
            entry={"enabled": True},
        )


@pytest.mark.parametrize("format", ["yaml", "", "JSON"])
def test_unknown_format_is_rejected(format: str) -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        merge("{}", format=format)
