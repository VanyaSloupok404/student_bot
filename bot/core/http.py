import aiohttp


class HttpClient:
    session: aiohttp.ClientSession | None = None

    @classmethod
    def get_session(cls) -> aiohttp.ClientSession:
        if cls.session is None or cls.session.closed:
            cls.session = aiohttp.ClientSession()
        return cls.session

    @classmethod
    async def close(cls) -> None:
        if cls.session and not cls.session.closed:
            await cls.session.close()
            cls.session = None


http_client = HttpClient()
