"""Run only with the pinned Selkies interpreter, isolated from service dependencies."""

import asyncio
import json
import sys

from selkies.display_utils import resize_display


async def main():
    width, height = map(int, sys.argv[1:3])
    result = await asyncio.wait_for(resize_display("%dx%d" % (width, height)), 2)
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
