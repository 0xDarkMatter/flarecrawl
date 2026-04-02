"""Tests for HTML extraction utilities."""

from flarecrawl.extract import (
    clean_content,
    extract_images,
    extract_main_content,
    extract_structured_data,
    filter_tags,
    html_to_markdown,
)


# ------------------------------------------------------------------
# extract_main_content
# ------------------------------------------------------------------


class TestExtractMainContent:

    def test_finds_main_tag(self):
        html = "<html><body><nav>Nav</nav><main><h1>Title</h1><p>Content here that is long enough to pass the threshold.</p></main><footer>Foot</footer></body></html>"
        result = extract_main_content(html)
        assert "Title" in result
        assert "Content here" in result
        assert "Nav" not in result
        assert "Foot" not in result

    def test_finds_article_tag(self):
        html = "<html><body><header>Head</header><article><h1>Article</h1><p>Article body text that is definitely long enough.</p></article><aside>Side</aside></body></html>"
        result = extract_main_content(html)
        assert "Article" in result
        assert "Article body" in result
        assert "Head" not in result
        assert "Side" not in result

    def test_finds_role_main(self):
        html = '<html><body><nav>Nav</nav><div role="main"><p>Main content that is long enough to pass the fifty character threshold easily.</p></div></body></html>'
        result = extract_main_content(html)
        assert "Main content" in result
        assert "Nav" not in result

    def test_fallback_strips_nav_footer(self):
        html = "<html><body><nav>Nav</nav><p>Paragraph content here.</p><footer>Foot</footer></body></html>"
        result = extract_main_content(html)
        assert "Paragraph" in result
        assert "Nav" not in result
        assert "Foot" not in result

    def test_strips_script_style(self):
        html = "<html><body><main><p>Content that is definitely long enough to pass the check.</p><script>alert('x')</script><style>.x{}</style></main></body></html>"
        result = extract_main_content(html)
        assert "Content" in result
        assert "alert" not in result
        assert ".x{}" not in result


# ------------------------------------------------------------------
# filter_tags
# ------------------------------------------------------------------


class TestFilterTags:

    def test_include_keeps_matching(self):
        html = '<html><body><div class="post">Post content</div><nav>Nav</nav><footer>Foot</footer></body></html>'
        result = filter_tags(html, include=[".post"])
        assert "Post content" in result
        assert "Nav" not in result
        assert "Foot" not in result

    def test_include_multiple_selectors(self):
        html = '<html><body><h1>Title</h1><div class="post">Post</div><p class="summary">Summary</p><nav>Nav</nav></body></html>'
        result = filter_tags(html, include=[".post", ".summary"])
        assert "Post" in result
        assert "Summary" in result
        assert "Nav" not in result

    def test_exclude_removes_matching(self):
        html = "<html><body><p>Content</p><nav>Nav</nav><footer>Foot</footer><aside>Side</aside></body></html>"
        result = filter_tags(html, exclude=["nav", "footer", "aside"])
        assert "Content" in result
        assert "Nav" not in result
        assert "Foot" not in result
        assert "Side" not in result

    def test_exclude_by_class(self):
        html = '<html><body><p>Content</p><div class="ads">Ad banner</div></body></html>'
        result = filter_tags(html, exclude=[".ads"])
        assert "Content" in result
        assert "Ad banner" not in result

    def test_no_filters_returns_body(self):
        html = "<html><body><p>Content</p></body></html>"
        result = filter_tags(html)
        assert "Content" in result


# ------------------------------------------------------------------
# extract_images
# ------------------------------------------------------------------


class TestExtractImages:

    def test_finds_img_tags(self):
        html = '<html><body><img src="/photo.jpg" alt="Photo" width="100" height="50"></body></html>'
        result = extract_images(html, "https://example.com")
        assert len(result) == 1
        assert result[0]["url"] == "https://example.com/photo.jpg"
        assert result[0]["alt"] == "Photo"
        assert result[0]["width"] == "100"
        assert result[0]["height"] == "50"

    def test_resolves_relative_urls(self):
        html = '<html><body><img src="images/photo.jpg"></body></html>'
        result = extract_images(html, "https://example.com/page/")
        assert result[0]["url"] == "https://example.com/page/images/photo.jpg"

    def test_finds_absolute_urls(self):
        html = '<html><body><img src="https://cdn.example.com/photo.jpg"></body></html>'
        result = extract_images(html, "https://example.com")
        assert result[0]["url"] == "https://cdn.example.com/photo.jpg"

    def test_finds_picture_source(self):
        html = '<html><body><picture><source srcset="/hero.webp 1x, /hero-2x.webp 2x"><img src="/hero.jpg"></picture></body></html>'
        result = extract_images(html, "https://example.com")
        urls = [img["url"] for img in result]
        assert "https://example.com/hero.webp" in urls
        assert "https://example.com/hero.jpg" in urls

    def test_finds_og_image(self):
        html = '<html><head><meta property="og:image" content="https://example.com/og.png"></head><body></body></html>'
        result = extract_images(html, "https://example.com")
        assert len(result) == 1
        assert result[0]["url"] == "https://example.com/og.png"

    def test_deduplicates(self):
        html = '<html><body><img src="/photo.jpg"><img src="/photo.jpg"></body></html>'
        result = extract_images(html, "https://example.com")
        assert len(result) == 1

    def test_finds_data_src(self):
        html = '<html><body><img data-src="/lazy.jpg"></body></html>'
        result = extract_images(html, "https://example.com")
        assert len(result) == 1
        assert result[0]["url"] == "https://example.com/lazy.jpg"

    def test_empty_html(self):
        result = extract_images("<html><body></body></html>", "https://example.com")
        assert result == []


# ------------------------------------------------------------------
# extract_structured_data
# ------------------------------------------------------------------


class TestExtractStructuredData:

    def test_ld_json_single(self):
        html = '''<html><head><script type="application/ld+json">{"@type": "Organization", "name": "Example"}</script></head><body></body></html>'''
        result = extract_structured_data(html)
        assert len(result["ld_json"]) == 1
        assert result["ld_json"][0]["@type"] == "Organization"
        assert result["ld_json"][0]["name"] == "Example"

    def test_ld_json_multiple(self):
        html = '''<html><head>
        <script type="application/ld+json">{"@type": "Organization", "name": "Org"}</script>
        <script type="application/ld+json">{"@type": "WebSite", "url": "https://example.com"}</script>
        </head><body></body></html>'''
        result = extract_structured_data(html)
        assert len(result["ld_json"]) == 2

    def test_ld_json_array(self):
        html = '''<html><head><script type="application/ld+json">[{"@type": "A"}, {"@type": "B"}]</script></head><body></body></html>'''
        result = extract_structured_data(html)
        assert len(result["ld_json"]) == 2

    def test_ld_json_malformed_skipped(self):
        html = '''<html><head><script type="application/ld+json">not valid json {{{</script></head><body></body></html>'''
        result = extract_structured_data(html)
        assert result["ld_json"] == []

    def test_opengraph(self):
        html = '''<html><head>
        <meta property="og:title" content="Example Page">
        <meta property="og:description" content="A description">
        <meta property="og:image" content="https://example.com/img.png">
        <meta property="og:url" content="https://example.com">
        </head><body></body></html>'''
        result = extract_structured_data(html)
        assert result["opengraph"]["title"] == "Example Page"
        assert result["opengraph"]["description"] == "A description"
        assert result["opengraph"]["image"] == "https://example.com/img.png"
        assert result["opengraph"]["url"] == "https://example.com"

    def test_twitter_card(self):
        html = '''<html><head>
        <meta name="twitter:card" content="summary_large_image">
        <meta name="twitter:title" content="Tweet Title">
        <meta name="twitter:site" content="@example">
        </head><body></body></html>'''
        result = extract_structured_data(html)
        assert result["twitter_card"]["card"] == "summary_large_image"
        assert result["twitter_card"]["title"] == "Tweet Title"
        assert result["twitter_card"]["site"] == "@example"

    def test_all_empty(self):
        result = extract_structured_data("<html><head></head><body></body></html>")
        assert result["ld_json"] == []
        assert result["opengraph"] == {}
        assert result["twitter_card"] == {}

    def test_mixed_content(self):
        html = '''<html><head>
        <script type="application/ld+json">{"@type": "Article", "headline": "Test"}</script>
        <meta property="og:title" content="OG Title">
        <meta name="twitter:card" content="summary">
        </head><body></body></html>'''
        result = extract_structured_data(html)
        assert len(result["ld_json"]) == 1
        assert result["opengraph"]["title"] == "OG Title"
        assert result["twitter_card"]["card"] == "summary"


# ------------------------------------------------------------------
# html_to_markdown
# ------------------------------------------------------------------


class TestHtmlToMarkdown:

    def test_headings(self):
        html = "<h1>Title</h1><h2>Subtitle</h2><h3>Section</h3>"
        result = html_to_markdown(html)
        assert "# Title" in result
        assert "## Subtitle" in result
        assert "### Section" in result

    def test_paragraphs(self):
        html = "<p>First paragraph.</p><p>Second paragraph.</p>"
        result = html_to_markdown(html)
        assert "First paragraph." in result
        assert "Second paragraph." in result

    def test_links(self):
        html = '<p>Visit <a href="https://example.com">Example</a> site.</p>'
        result = html_to_markdown(html)
        assert "[Example](https://example.com)" in result

    def test_bold_italic(self):
        html = "<p><strong>Bold</strong> and <em>italic</em> text.</p>"
        result = html_to_markdown(html)
        assert "**Bold**" in result
        assert "*italic*" in result

    def test_unordered_list(self):
        html = "<ul><li>One</li><li>Two</li><li>Three</li></ul>"
        result = html_to_markdown(html)
        assert "- One" in result
        assert "- Two" in result
        assert "- Three" in result

    def test_ordered_list(self):
        html = "<ol><li>First</li><li>Second</li></ol>"
        result = html_to_markdown(html)
        assert "1. First" in result
        assert "2. Second" in result

    def test_code_block(self):
        html = "<pre>const x = 1;</pre>"
        result = html_to_markdown(html)
        assert "```" in result
        assert "const x = 1;" in result

    def test_strips_scripts(self):
        html = "<p>Content</p><script>alert('xss')</script>"
        result = html_to_markdown(html)
        assert "Content" in result
        assert "alert" not in result

    def test_blockquote(self):
        html = "<blockquote>Quoted text</blockquote>"
        result = html_to_markdown(html)
        assert "> Quoted text" in result


# ------------------------------------------------------------------
# TestCleanContent
# ------------------------------------------------------------------


class TestCleanContent:
    """Test ad/nav cruft removal from markdown text."""

    def test_removes_advertisement(self):
        text = "# Title\n\nAdvertisement\n\nThe actual article content here."
        result = clean_content(text)
        assert "Advertisement" not in result
        assert "actual article content" in result

    def test_removes_share_buttons(self):
        text = "Article text.\n\nShare this article\n\nMore article text."
        result = clean_content(text)
        assert "Share this article" not in result
        assert "Article text" in result

    def test_removes_newsletter_prompts(self):
        text = "Content.\n\nSign up\n\nNewsletter\n\nMore content."
        result = clean_content(text)
        assert "Sign up" not in result
        assert "Newsletter" not in result

    def test_preserves_article_content(self):
        text = "# The Future of AI\n\nArtificial intelligence is changing how we work and live.\n\nResearchers at MIT published new findings."
        result = clean_content(text)
        assert result == text

    def test_removes_copyright_lines(self):
        text = "Article content.\n\n\u00a9 2026 All Rights Reserved\n\nMore text."
        result = clean_content(text)
        assert "All Rights Reserved" not in result

    def test_preserves_long_lines_with_keywords(self):
        # Long lines that happen to contain cruft words should be preserved
        text = "The company decided to share this information with investors, noting that advertisement revenue was up 20% year over year."
        result = clean_content(text)
        assert "share this information" in result

    def test_collapses_blank_lines(self):
        text = "Title\n\nAdvertisement\n\n\n\nContent"
        result = clean_content(text)
        assert "\n\n\n" not in result

    def test_empty_input(self):
        assert clean_content("") == ""

    def test_removes_open_in_app(self):
        text = "Open in app\n\n# Article Title\n\nContent here."
        result = clean_content(text)
        assert "Open in app" not in result
        assert "Article Title" in result
