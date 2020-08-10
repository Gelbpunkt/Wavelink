"""MIT License

Copyright (c) 2019-2020 PythonistaGuild

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
import inspect
import logging

from urllib.parse import quote

from .errors import *
from .player import Track, TrackPlaylist
from .websocket import WebSocket

__log__ = logging.getLogger(__name__)


class Node:
    """A WaveLink Node instance.

    .. warning::
        You should not create :class:`Node` objects manually. Instead you should use, :func:`Client.initiate_node`.

    Attributes
    ------------
    host: str
        The host address the node is connected to.
    port: int
        The port the node is connected to.
    rest_uri: str
        The rest server address the node is connecte to.
    region: str
        The region provided to the node on connection.
    identifier: str
        The unique indentifier associated with the node.
    """

    def __init__(
        self,
        host,
        port,
        shards,
        user_id,
        *,
        client,
        session,
        rest_uri,
        password,
        region,
        identifier,
        shard_id=None,
        secure=False,
        heartbeat=None,
    ):
        self.host = host
        self.port = port
        self.rest_uri = rest_uri
        self.shards = shards
        self.uid = user_id
        self.password = password
        self.region = region
        self.identifier = identifier
        self.secure = secure
        self.heartbeat = heartbeat

        self.shard_id = shard_id

        self.players = {}

        self.session = session
        self._websocket = None
        self._client = client

        self.hook = None

        self.available = True

        self.stats = None

    def __repr__(self):
        return f"{self.identifier} | {self.region} | (Shard: {self.shard_id})"

    @property
    def is_available(self):
        """Return whether the Node is available or not."""
        return (
            self._websocket is not None
            and self._websocket.is_connected
            and self.available
        )

    def close(self):
        """Close the node and make it unavailable."""
        self.available = False

    def open(self):
        """Open the node and make it available."""
        self.available = True

    @property
    def penalty(self):
        """Returns the load-balancing penalty for this node."""
        if not self.available or not self.stats:
            return 9e30
        return self.stats.penalty.total

    async def connect(self):
        self._websocket = WebSocket(
            node=self,
            host=self.host,
            port=self.port,
            password=self.password,
            shard_count=self.shards,
            user_id=self.uid,
            secure=self.secure,
        )
        await self._websocket._connect()

        __log__.info(f"NODE | {self.identifier} connected:: {self.__repr__()}")

    async def get_tracks(self, query):
        """|coro|

        Search for and return a list of Tracks for the given query.

        Parameters
        ------------
        query: str
            The query to use to search for tracks. If a valid URL is not provided, it's best to default to
            "ytsearch:query", which allows the REST server to search YouTube for Tracks.

        Returns
        ---------
        Union[list, TrackPlaylist, None]:
            A list of or TrackPlaylist instance of :class:`wavelink.player.Track` objects.
            This could be None if no tracks were found.
        """
        retries = 5

        while retries > 0:
            async with self.session.get(
                f"{self.rest_uri}/loadtracks?identifier={quote(query)}",
                headers={"Authorization": self.password},
            ) as resp:
                data = await resp.json()

            if resp.status != 200:
                retries -= 1
            else:
                break

            await asyncio.sleep(0.2)

        if retries == 0 or not data.get("tracks", None):
            __log__.info(f"REST | No tracks with query:: <{query}> found.")
            return None

        if data["playlistInfo"]:
            return TrackPlaylist(data=data)

        tracks = []
        for track in data["tracks"]:
            tracks.append(Track(id_=track["track"], info=track["info"]))

        __log__.debug(
            f"REST | Found <{len(tracks)}> tracks with query:: <{query}>"
            f" ({self.__repr__()})"
        )

        return tracks

    async def build_track(self, identifier):
        """|coro|

        Build a track object with a valid track identifier.

        Parameters
        ------------
        identifier: str
            The tracks unique Base64 encoded identifier. This is usually retrieved from various lavalink events.

        Returns
        ---------
        :class:`wavelink.player.Track`
            The track built from a Base64 identifier.

        Raises
        --------
        BuildTrackError
            Decoding and building the track failed.
        """
        async with self.session.get(
            f"{self.rest_uri}/decodetrack",
            headers={"Authorization": self.password},
            params={"track": identifier},
        ) as resp:
            data = await resp.json()

            if not resp.status == 200:
                raise BuildTrackError(
                    f'Failed to build track. Status: {data["status"]}, Error:'
                    f' {data["error"]}.Check the identfier is correct and try again.'
                )

            track = Track(id_=identifier, info=data)
            return track

    def get_player(self, guild_id):
        """Retrieve a player object associated with the Node.

        Parameters
        ------------
        guild_id: int
            The guild id belonging to the player.

        Returns
        ---------
        Optional[Player]
        """
        return self.players.get(guild_id, None)

    async def on_event(
        self, event,
    ):
        """Function which dispatches events when triggered on the Node."""
        __log__.info(f"NODE | Event dispatched:: <{str(event)}> ({self.__repr__()})")
        if event.player:
            await event.player.hook(event)

        if not self.hook:
            return

        if inspect.iscoroutinefunction(self.hook):
            await self.hook(event)
        else:
            self.hook(event)

    def set_hook(
        self, func,
    ):
        """Set the Node Event Hook.

        The event hook will be dispatched when an Event occurs.
        Maybe a coroutine.

        Raises
        --------
        WavelinkException
            The hook provided was not a valid callable.
        """
        if not callable(func):
            raise WavelinkException("Node hook must be a callable.")

        self.hook = func

    async def destroy(self):
        """Destroy the node and all it's players."""
        players = self.players.copy()

        for player in players.values():
            await player.destroy()

        try:
            if self._websocket and self._websocket._task:
                self._websocket._task.cancel()
        except Exception:
            pass

        del self._client.nodes[self.identifier]

    async def _send(self, **data):
        __log__.debug(f"NODE | Sending payload:: <{data}> ({self.__repr__()})")
        if self._websocket:
            await self._websocket._send(**data)
