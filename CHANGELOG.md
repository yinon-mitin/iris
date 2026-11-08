# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Links to posts on X (Twitter) and Threads: the video, or all the photos as an album, is sent to the chat. X videos still go through yt-dlp; photos of an X post come from the FxTwitter API, and Threads posts are read from their public embed page (`src/engine/social.py`).
- `is_x` and `is_threads` link checks, a "no photo or video in this post" message, and tests for both (`tests/test_social.py`).
- X and Threads in the bot help text and in the English and Russian docs.

### Changed
- The known-limitations note about image-only X posts is replaced by a note on the FxTwitter and Threads embed dependencies.
