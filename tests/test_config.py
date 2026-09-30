import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from uptime_monitor.config import ConfigError, load_config, parse_config


class ParseConfigTest(unittest.TestCase):
    def test_defaults_are_applied(self):
        config = parse_config({
            "defaults": {"interval": 30},
            "sites": [{"name": "a", "url": "https://a.com"}],
        })
        self.assertEqual(config.sites[0].interval, 30)
        self.assertEqual(config.sites[0].timeout, 10)

    def test_site_overrides_defaults(self):
        config = parse_config({
            "defaults": {"interval": 30},
            "sites": [{"name": "a", "url": "https://a.com", "interval": 120}],
        })
        self.assertEqual(config.sites[0].interval, 120)

    def test_name_defaults_to_url(self):
        config = parse_config({"sites": [{"url": "https://a.com"}]})
        self.assertEqual(config.sites[0].name, "https://a.com")

    def test_errors(self):
        bad_configs = [
            {},                                                         # no sites
            {"sites": [{"name": "a"}]},                                 # no url
            {"sites": [{"url": "a.com"}]},                              # no scheme
            {"sites": [{"url": "https://a.com", "interval": 1}]},       # too frequent
            {"sites": [{"url": "https://a.com", "typo_field": 1}]},     # unknown field
            {"sites": [{"name": "x", "url": "https://a.com"},
                       {"name": "x", "url": "https://b.com"}]},         # duplicate name
        ]
        for data in bad_configs:
            with self.subTest(data=data), self.assertRaises(ConfigError):
                parse_config(data)

    def test_telegram_from_env(self):
        env = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "42"}
        with mock.patch.dict(os.environ, env):
            config = parse_config({"sites": [{"url": "https://a.com"}]})
        self.assertEqual(config.telegram.chat_id, "42")

    def test_no_telegram_without_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            config = parse_config({"sites": [{"url": "https://a.com"}]})
        self.assertIsNone(config.telegram)


class LoadConfigTest(unittest.TestCase):
    def test_example_config_is_valid(self):
        example = Path(__file__).parent.parent / "config.example.toml"
        config = load_config(example)
        self.assertGreater(len(config.sites), 0)

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config("does-not-exist.toml")

    def test_invalid_toml(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write("this is = = not toml")
        try:
            with self.assertRaises(ConfigError):
                load_config(f.name)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
