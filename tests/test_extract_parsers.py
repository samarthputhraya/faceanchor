"""Tests for the HTML extraction parsers in faceanchor.extract.post."""

from faceanchor.extract.post import Post, meta_tags, json_ld, from_html


def test_meta_tags_reads_og_metadata_and_decodes_html_escapes():
    html = """
    <html>
      <head>
        <meta property="og:title" content="Hello & World" />
        <meta property="og:description" content="Description with & entity" />
        <meta property="og:image" content="https://example.com/img.jpg" />
      </head>
    </html>
    """
    og = meta_tags(html)
    assert og["og:title"] == "Hello & World"
    assert og["og:description"] == "Description with & entity"
    assert og["og:image"] == "https://example.com/img.jpg"


def test_json_ld_parses_json_ld_blocks():
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "SocialMediaPosting",
          "author": {"@type": "Person", "name": "Jane Doe"},
          "datePublished": "2024-01-15T12:30:00Z"
        }
        </script>
      </head>
    </html>
    """
    blocks = json_ld(html)
    assert len(blocks) == 1
    assert blocks[0]["author"]["name"] == "Jane Doe"
    assert blocks[0]["datePublished"] == "2024-01-15T12:30:00Z"


def test_malformed_json_ld_is_skipped_instead_of_crashing():
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        { this is not valid json }
        </script>
        <script type="application/ld+json">
        {"valid": "block"}
        </script>
      </head>
    </html>
    """
    blocks = json_ld(html)
    assert len(blocks) == 1
    assert blocks[0]["valid"] == "block"


def test_author_comes_from_json_ld_rather_than_og_title_linkedin_regression():
    html = """
    <html>
      <head>
        <meta property="og:title" content="This is the post text, not the author" />
        <meta property="og:description" content="Post description" />
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "SocialMediaPosting",
          "author": {"@type": "Person", "name": "Real Author"},
          "datePublished": "2024-01-15T12:30:00Z"
        }
        </script>
      </head>
    </html>
    """
    post = Post(url="https://linkedin.com/posts/123", platform="linkedin")
    from_html(html, post)
    assert post.author == "Real Author"
    assert post.caption == "Post description"


def test_image_url_comes_from_og_image():
    html = """
    <html>
      <head>
        <meta property="og:image" content="https://cdn.example.com/photo.jpg" />
        <meta property="og:title" content="Post Title" />
      </head>
    </html>
    """
    post = Post(url="https://example.com/post", platform="instagram")
    from_html(html, post)
    assert post.image_url == "https://cdn.example.com/photo.jpg"
    assert post.image_source == "post_og"


def test_page_with_no_useful_metadata_leaves_post_empty():
    html = """
    <html>
      <head>
        <title>Plain Page</title>
      </head>
      <body>
        <p>No OpenGraph or JSON-LD here.</p>
      </body>
    </html>
    """
    post = Post(url="https://example.com/plain", platform="unknown")
    from_html(html, post)
    assert post.author == ""
    assert post.caption == ""
    assert post.image_url == ""
    assert post.posted_at == ""
    assert post.posted_at_source == "unknown"