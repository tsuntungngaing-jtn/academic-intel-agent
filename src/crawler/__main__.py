"""OpenAlex crawl (legacy entry; prefer ``python main.py crawl``)."""

from storage.crawl_jsonl import run_crawl_main

if __name__ == "__main__":
    run_crawl_main()
