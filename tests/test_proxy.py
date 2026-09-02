from cutai.proxy import normalize_proxy_url


def test_accepts_complete_url():
    assert normalize_proxy_url("http://user:pass@host:1234") == "http://user:pass@host:1234"


def test_converts_bright_data_colon_format():
    value = normalize_proxy_url("brd.example:33335:customer-zone:pa:ss")
    assert value == "http://customer-zone:pa%3Ass@brd.example:33335"


def test_converts_curl_snippet():
    value = normalize_proxy_url("curl --proxy brd.example:33335 --proxy-user user:pass https://example.com")
    assert value == "http://user:pass@brd.example:33335"

