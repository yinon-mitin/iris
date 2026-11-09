"""Texts the user sees. Needs the same environment variables as the other tests."""
import unittest

from config import BotText


class UnsupportedLinkTextTest(unittest.TestCase):
    def test_does_not_look_like_a_bot_for_russian_sites_only(self):
        text = BotText.error_unsupported
        for site in ("YouTube", "TikTok", "Instagram", "VK"):
            with self.subTest(site=site):
                self.assertIn(site, text)

    def test_blames_the_link_not_the_whole_site(self):
        self.assertNotIn("Этот сайт я не умею", BotText.error_unsupported)


if __name__ == "__main__":
    unittest.main()
