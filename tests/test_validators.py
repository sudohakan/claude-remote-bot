"""Tests for security/validators.py."""

import pytest

from src.security.validators import (
    MAX_UPLOAD_BYTES,
    PathValidator,
    sanitize_text,
    validate_filename,
    validate_upload_size,
)


@pytest.fixture
def sandbox(tmp_path):
    return PathValidator(sandbox_root=tmp_path)


# ── PathValidator ─────────────────────────────────────────────────────────────


class TestPathValidator:
    def test_valid_path_within_root(self, sandbox, tmp_path):
        ok, resolved, err = sandbox.validate("subdir/file.py")
        assert ok is True
        assert resolved is not None
        assert err is None

    def test_path_traversal_blocked(self, sandbox):
        ok, resolved, err = sandbox.validate("../../etc/passwd")
        assert ok is False
        assert resolved is None
        assert err is not None

    def test_double_dot_blocked(self, sandbox):
        ok, _, err = sandbox.validate("../outside")
        assert ok is False

    def test_null_byte_blocked(self, sandbox):
        ok, _, err = sandbox.validate("file\x00.txt")
        assert ok is False

    def test_variable_expansion_blocked(self, sandbox):
        ok, _, err = sandbox.validate("${HOME}/secret")
        assert ok is False

    def test_backtick_blocked(self, sandbox):
        ok, _, err = sandbox.validate("`rm -rf /`")
        assert ok is False

    def test_absolute_path_within_root(self, sandbox, tmp_path):
        target = str(tmp_path / "subdir" / "file.py")
        ok, resolved, err = sandbox.validate(target)
        assert ok is True

    def test_absolute_path_outside_root_blocked(self, sandbox):
        ok, _, err = sandbox.validate("/etc/shadow")
        assert ok is False

    def test_empty_path_blocked(self, sandbox):
        ok, _, err = sandbox.validate("")
        assert ok is False

    def test_whitespace_only_blocked(self, sandbox):
        ok, _, err = sandbox.validate("   ")
        assert ok is False

    def test_relative_to_override(self, sandbox, tmp_path):
        sub = tmp_path / "project"
        sub.mkdir()
        ok, resolved, err = sandbox.validate("file.py", relative_to=sub)
        assert ok is True
        assert resolved.parent == sub


# ── validate_filename ─────────────────────────────────────────────────────────


class TestValidateFilename:
    def test_valid_python_file(self):
        ok, err = validate_filename("main.py")
        assert ok is True
        assert err is None

    def test_valid_markdown(self):
        ok, _ = validate_filename("README.md")
        assert ok is True

    def test_blocked_env_file(self):
        ok, err = validate_filename(".env")
        assert ok is False

    def test_blocked_ssh_key(self):
        ok, _ = validate_filename("id_rsa")
        assert ok is False

    def test_hidden_file_blocked(self):
        ok, _ = validate_filename(".secret")
        assert ok is False

    def test_gitignore_allowed(self):
        ok, _ = validate_filename(".gitignore")
        assert ok is True

    def test_path_separators_blocked(self):
        ok, _ = validate_filename("../evil.py")
        assert ok is False

    def test_disallowed_extension(self):
        ok, err = validate_filename("malware.exe")
        assert ok is False

    def test_empty_blocked(self):
        ok, _ = validate_filename("")
        assert ok is False

    def test_too_long_blocked(self):
        name = "a" * 300 + ".py"
        ok, _ = validate_filename(name)
        assert ok is False

    def test_null_byte_blocked(self):
        ok, _ = validate_filename("file\x00.py")
        assert ok is False


# ── validate_upload_size ─────────────────────────────────────────────────────


class TestUploadSize:
    def test_small_file_ok(self):
        ok, err = validate_upload_size(1024)
        assert ok is True
        assert err is None

    def test_exactly_max_ok(self):
        ok, _ = validate_upload_size(MAX_UPLOAD_BYTES)
        assert ok is True

    def test_over_max_rejected(self):
        ok, err = validate_upload_size(MAX_UPLOAD_BYTES + 1)
        assert ok is False
        assert "MB" in err


# ── sanitize_text ─────────────────────────────────────────────────────────────


class TestSanitizeText:
    def test_strips_null_bytes(self):
        result = sanitize_text("hello\x00world")
        assert "\x00" not in result
        assert "helloworld" == result

    def test_truncates_to_max(self):
        long_text = "a" * 5000
        result = sanitize_text(long_text, max_length=100)
        assert len(result) == 100

    def test_normal_text_unchanged(self):
        text = "This is fine."
        assert sanitize_text(text) == text
