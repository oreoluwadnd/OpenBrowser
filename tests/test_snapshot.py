from openbrowser.snapshot import compact

RAW = """\
- generic [ref=e2]:
  - generic [ref=e3]:
    - link "Sign in" [ref=e19] [cursor=pointer]:
      - /url: /login?return_to=https%3A%2F%2Fexample.com
    - generic [ref=e20]: Deliver to
    - generic [ref=e21] [cursor=pointer]: Menu
  - list [ref=e29]:
    - listitem [ref=e30]:
      - link [ref=e41] [cursor=pointer]:
        - /url: https://example.com/a
        - text: Product A"""

EXPECTED = """\
- link "Sign in" [ref=e19]
- generic [ref=e20]: Deliver to
- generic [ref=e21] [cursor=pointer]: Menu
- list [ref=e29]:
  - listitem [ref=e30]:
    - link [ref=e41]:
      - text: Product A"""


def test_compact():
    assert compact(RAW) == EXPECTED
