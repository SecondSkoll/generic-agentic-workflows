"""Check whether a webpage is reachable and returns a successful response.

Run this script with a URL::

	python test-script.py https://example.com
"""

import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def is_valid_webpage(url: str) -> bool:
	"""Return ``True`` when *url* responds with an HTTP 2xx status code."""
	request = Request(url, method="HEAD", headers={"User-Agent": "webpage-validator/1.0"})

	try:
		with urlopen(request, timeout=10) as response:
			return 200 <= response.status < 300
	except HTTPError as error:
		print(f"Invalid webpage: {url} returned HTTP {error.code}.")
	except URLError as error:
		print(f"Invalid webpage: could not reach {url} ({error.reason}).")

	return False


def main() -> None:
	"""Validate the URL supplied as the first command-line argument."""
	if len(sys.argv) != 2:
		print("Usage: python test-script.py <url>")
		raise SystemExit(2)

	url = sys.argv[1]
	if is_valid_webpage(url):
		print(f"Valid webpage: {url}")
		return

	raise SystemExit(1)


if __name__ == "__main__":
	# Run only when this file is executed directly, not when it is imported.
	main()
