import random
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from os import chdir
from os.path import dirname, realpath
from pathlib import Path
from subprocess import run
from unittest.mock import MagicMock, Mock

import pytest
import pytz
from callee import Matching, String

import turbot

CLIENT_TOKEN = "my-token"
CLIENT_USER = "ADMIN"
CLIENT_USER_ID = 82226367030108160

AUTHORIZED_CHANNEL = "good channel"
UNAUTHORIZED_CHANNEL = "bad channel"

NOW = datetime(year=1982, month=4, day=24, tzinfo=pytz.utc)

SRC_ROOT = Path(dirname(realpath(__file__))).parent
SRC_DIRS = ["tests", "turbot", "scripts"]


class Member:
    def __init__(self, member_name, member_id):
        self.name = member_name
        self.id = member_id

    def __repr__(self):
        return f"{self.name}#{self.id}"


ADMIN = Member(CLIENT_USER, CLIENT_USER_ID)
FRIEND = Member("friend", 82169952898912256)
BUDDY = Member("buddy", 82942320688758784)
GUY = Member("guy", 82988021019836416)
CHANNEL_MEMBERS = [FRIEND, BUDDY, GUY, ADMIN]

PUNK = Member("punk", 119678027792646146)

S_SPY = Mock(wraps=turbot.s)


def someone():
    """Returns some non-admin user"""
    return random.choice(list(filter(lambda member: member != ADMIN, CHANNEL_MEMBERS)))


def somebells():
    """Returns some random amount of bells"""
    return random.randint(100, 500)


def is_discord_file(obj):
    """Returns true if the given object is a discord File object."""
    return (obj.__class__.__name__) == "File"


class Channel:
    def __init__(self, channel_type, channel_name):
        self.type = channel_type
        self.name = channel_name
        self.sent = MagicMock()
        self.members = CHANNEL_MEMBERS

    async def send(self, response, file):
        self.sent(response, file)

    @asynccontextmanager
    async def typing(self):
        yield


class Message:
    def __init__(self, author, channel, content):
        self.author = author
        self.channel = channel
        self.content = content


class MockClient:
    def __init__(self):
        self.user = ADMIN


@pytest.fixture
def patch_discord():
    orig = turbot.Turbot.__bases__
    turbot.Turbot.__bases__ = (MockClient,)
    yield
    turbot.Turbot.__bases__ = orig


@pytest.fixture(autouse=True, scope="session")
def set_random_seed():
    random.seed(0)


@pytest.fixture
def client(monkeypatch, freezer, patch_discord, tmp_path):
    monkeypatch.setattr(turbot, "GRAPHCMD_FILE", tmp_path / "graphcmd.png")
    monkeypatch.setattr(turbot, "LASTWEEKCMD_FILE", tmp_path / "lastweek.png")
    monkeypatch.setattr(turbot, "s", S_SPY)
    freezer.move_to(NOW)
    return turbot.Turbot(
        token=CLIENT_TOKEN,
        channels=[AUTHORIZED_CHANNEL],
        prices_file=tmp_path / "prices.csv",
        fossils_file=tmp_path / "fossils.csv",
        users_file=tmp_path / "users.csv",
    )


@pytest.fixture
def lines():
    wrote_lines = defaultdict(int)

    def next(path):
        with open(path, "r") as f:
            rvalue = f.readlines()[wrote_lines[path] :]
            wrote_lines[path] += len(rvalue)
            return rvalue

    return next


@pytest.fixture
def graph(mocker, monkeypatch):
    def create_file(*args, **kwargs):
        Path(turbot.GRAPHCMD_FILE).touch()

    mock = mocker.Mock(side_effect=create_file)
    monkeypatch.setattr(turbot.Turbot, "generate_graph", mock)
    return mock


@pytest.fixture
def lastweek(mocker, monkeypatch):
    def create_file(*args, **kwargs):
        Path(turbot.LASTWEEKCMD_FILE).touch()

    mock = mocker.Mock(side_effect=create_file)
    monkeypatch.setattr(turbot.Turbot, "generate_graph", mock)
    return mock


@pytest.mark.asyncio
class TestTurbot:
    async def test_init(self, client):
        assert client.token == CLIENT_TOKEN
        assert client.channels == [AUTHORIZED_CHANNEL]

    async def test_on_ready(self, client):
        await client.on_ready()

    async def test_on_message_non_text(self, client):
        invalid_channel_type = "voice"
        channel = Channel(invalid_channel_type, AUTHORIZED_CHANNEL)
        author = someone()
        message = Message(author, channel, "!help")
        await client.on_message(message)
        channel.sent.assert_not_called()

    async def test_on_message_from_admin(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        message = Message(ADMIN, channel, "!help")
        await client.on_message(message)
        channel.sent.assert_not_called()

    async def test_on_message_in_unauthorized_channel(self, client):
        channel = Channel("text", UNAUTHORIZED_CHANNEL)
        author = someone()
        message = Message(author, channel, "!help")
        await client.on_message(message)
        channel.sent.assert_not_called()

    async def test_on_message_ambiguous_request(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()
        message = Message(author, channel, "!h")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Did you mean: !help, !hemisphere, !history?", None,
        )

    async def test_on_message_sell_no_price(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()
        message = Message(author, channel, "!sell")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Please include selling price after command name.", None
        )

    async def test_on_message_sell_nonnumeric_price(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()
        message = Message(author, channel, "!sell foot")
        await client.on_message(message)
        channel.sent.assert_called_with("Selling price must be a number.", None)

    async def test_on_message_sell_nonpositive_price(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()
        message = Message(author, channel, "!sell 0")
        await client.on_message(message)
        channel.sent.assert_called_with("Selling price must be greater than zero.", None)

    async def test_on_message_sell(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        # initial sale
        amount = somebells()
        message = Message(author, channel, f"!sell {amount}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"Logged selling price of {amount} for user {author}.", None
        )
        assert lines(client.prices_file) == [
            "author,kind,price,timestamp\n",
            f"{author.id},sell,{amount},{NOW}\n",
        ]

        # same price sale
        message = Message(author, channel, f"!sell {amount}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            (
                f"Logged selling price of {amount} for user {author}. "
                f"(Same as last selling price)"
            ),
            None,
        )
        assert lines(client.prices_file) == [f"{author.id},sell,{amount},{NOW}\n"]

        # higher price sale
        new_amount = amount + somebells()
        message = Message(author, channel, f"!sell {new_amount}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            (
                f"Logged selling price of {new_amount} for user {author}. "
                f"(Higher than last selling price of {amount} bells)"
            ),
            None,
        )
        assert lines(client.prices_file) == [f"{author.id},sell,{new_amount},{NOW}\n"]

        # lower price sale
        last_amount = round(amount / 2)
        message = Message(author, channel, f"!sell {last_amount}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            (
                f"Logged selling price of {last_amount} for user {author}. "
                f"(Lower than last selling price of {new_amount} bells)"
            ),
            None,
        )
        assert lines(client.prices_file) == [f"{author.id},sell,{last_amount},{NOW}\n"]

    async def test_on_message_buy_no_price(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()
        message = Message(author, channel, "!buy")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Please include buying price after command name.", None
        )

    async def test_on_message_buy_nonnumeric_price(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()
        message = Message(author, channel, "!buy foot")
        await client.on_message(message)
        channel.sent.assert_called_with("Buying price must be a number.", None)

    async def test_on_message_buy_nonpositive_price(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()
        message = Message(author, channel, "!buy 0")
        await client.on_message(message)
        channel.sent.assert_called_with("Buying price must be greater than zero.", None)

    async def test_on_message_buy(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()
        amount = somebells()
        message = Message(author, channel, f"!buy {amount}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"Logged buying price of {amount} for user {author}.", None
        )
        assert lines(client.prices_file) == [
            "author,kind,price,timestamp\n",
            f"{author.id},buy,{amount},{NOW}\n",
        ]

    async def test_on_message_help(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()
        message = Message(author, channel, "!help")
        await client.on_message(message)
        channel.sent.assert_called_with(String(), None)  # TODO: Verify help response?

    async def test_on_message_clear(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        # first log some buy and sell prices
        await client.on_message(Message(author, channel, f"!buy {somebells()}"))
        await client.on_message(Message(author, channel, f"!sell {somebells()}"))
        await client.on_message(Message(author, channel, f"!sell {somebells()}"))

        # then ensure we can clear them all out
        message = Message(author, channel, "!clear")
        await client.on_message(message)
        channel.sent.assert_called_with(f"**Cleared history for {author}.**", None)
        assert lines(client.prices_file) == ["author,kind,price,timestamp\n"]

    async def test_on_message_bestsell(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # first log some buy and sell prices
        await client.on_message(Message(FRIEND, channel, "!buy 100"))
        await client.on_message(Message(FRIEND, channel, "!sell 200"))
        await client.on_message(Message(BUDDY, channel, "!buy 120"))
        await client.on_message(Message(BUDDY, channel, "!sell 90"))
        await client.on_message(Message(BUDDY, channel, "!sell 600"))
        await client.on_message(Message(GUY, channel, "!buy 800"))

        # then ensure we can find the best sell
        author = someone()
        message = Message(author, channel, "!bestsell")
        await client.on_message(message)
        channel.sent.assert_called_with(
            (
                "__**Best Selling Prices in the Last 12 Hours**__\n"
                f"> {BUDDY}: 600 bells at {NOW}\n"
                f"> {FRIEND}: 200 bells at {NOW}"
            ),
            None,
        )

    async def test_on_message_oops(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        # first log some buy and sell prices
        await client.on_message(Message(author, channel, "!buy 1"))
        await client.on_message(Message(author, channel, "!sell 2"))
        await client.on_message(Message(author, channel, "!buy 3"))

        # then ensure we can remove the last entered price
        message = Message(author, channel, "!oops")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"**Deleting last logged price for {author}.**", None
        )
        assert lines(client.prices_file) == [
            "author,kind,price,timestamp\n",
            f"{author.id},buy,1,{NOW}\n",
            f"{author.id},sell,2,{NOW}\n",
        ]

    async def test_on_message_history_bad_name(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        # first log some buy and sell prices
        await client.on_message(Message(author, channel, "!buy 1"))
        await client.on_message(Message(author, channel, "!sell 2"))
        await client.on_message(Message(author, channel, "!buy 3"))

        # then try to get history for a user that isn't there
        message = Message(author, channel, f"!history {PUNK.name}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"Can not find the user named {PUNK.name} in this channel.", None
        )

    async def test_on_message_history_without_name(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        # first log some buy and sell prices
        await client.on_message(Message(author, channel, "!buy 1"))
        await client.on_message(Message(author, channel, "!sell 2"))
        await client.on_message(Message(author, channel, "!buy 3"))

        # then ensure we can the get history
        message = Message(author, channel, "!history")
        await client.on_message(message)
        channel.sent.assert_called_with(
            (
                f"__**Historical info for {author}**__\n"
                f"> Can buy turnips from Daisy Mae for 1 bells at {NOW}\n"
                f"> Can sell turnips to Timmy & Tommy for 2 bells at {NOW}\n"
                f"> Can buy turnips from Daisy Mae for 3 bells at {NOW}"
            ),
            None,
        )

    async def test_on_message_history_with_name(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # first log some buy and sell prices
        await client.on_message(Message(BUDDY, channel, "!buy 1"))
        await client.on_message(Message(BUDDY, channel, "!sell 2"))
        await client.on_message(Message(BUDDY, channel, "!buy 3"))

        # then ensure we can the get history
        message = Message(GUY, channel, f"!history {BUDDY.name}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            (
                f"__**Historical info for {BUDDY}**__\n"
                f"> Can buy turnips from Daisy Mae for 1 bells at {NOW}\n"
                f"> Can sell turnips to Timmy & Tommy for 2 bells at {NOW}\n"
                f"> Can buy turnips from Daisy Mae for 3 bells at {NOW}"
            ),
            None,
        )

    async def test_on_message_bestbuy(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # first log some buy and sell prices
        await client.on_message(Message(FRIEND, channel, "!buy 100"))
        await client.on_message(Message(FRIEND, channel, "!sell 600"))
        await client.on_message(Message(BUDDY, channel, "!buy 60"))
        await client.on_message(Message(BUDDY, channel, "!sell 90"))
        await client.on_message(Message(BUDDY, channel, "!sell 200"))
        await client.on_message(Message(GUY, channel, "!sell 800"))

        # then ensure we can find the best buy
        author = someone()
        message = Message(author, channel, "!bestbuy")
        await client.on_message(message)
        channel.sent.assert_called_with(
            (
                "__**Best Buying Prices in the Last 12 Hours**__\n"
                f"> {BUDDY}: 60 bells at {NOW}\n"
                f"> {FRIEND}: 100 bells at {NOW}"
            ),
            None,
        )

    async def test_on_message_turnippattern_happy_paths(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        message = Message(someone(), channel, "!turnippattern 100 86")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Based on your prices, you will see one of the following patterns this week:\n> **Decreasing**: Prices will continuously fall.\n> **Small Spike**: Prices fall until a spike occurs. The price will go up three more times. Sell on the third increase for maximum profit. Spikes only occur from Monday to Thursday.\n> **Big Spike**: Prices fall until a small spike. Prices then decrease before shooting up twice. Sell the second time prices shoot up after the decrease for maximum profit. Spikes only occur from Monday to Thursday.",  # noqa: E501
            None,
        )

        message = Message(someone(), channel, "!turnippattern 100 99")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Based on your prices, you will see one of the following patterns this week:\n> **Random**: Prices are completely random. Sell when it goes over your buying price.\n> **Big Spike**: Prices fall until a small spike. Prices then decrease before shooting up twice. Sell the second time prices shoot up after the decrease for maximum profit. Spikes only occur from Monday to Thursday.",  # noqa: E501
            None,
        )

        message = Message(someone(), channel, "!turnippattern 100 22")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Based on your prices, you will see one of the following patterns this week:\n> **Big Spike**: Prices fall until a small spike. Prices then decrease before shooting up twice. Sell the second time prices shoot up after the decrease for maximum profit. Spikes only occur from Monday to Thursday.",  # noqa: E501
            None,
        )

    async def test_on_message_turnippattern_invalid_params(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        message = Message(someone(), channel, "!turnippattern 100")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Please provide Daisy Mae's price and your Monday morning price\n"
            "eg. !turnippattern <buy price> <Monday morning sell price>",
            None,
        )

        message = Message(someone(), channel, "!turnippattern 1 2 3")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Please provide Daisy Mae's price and your Monday morning price\n"
            "eg. !turnippattern <buy price> <Monday morning sell price>",
            None,
        )

    async def test_on_message_turnippattern_nonnumeric_prices(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        message = Message(someone(), channel, "!turnippattern something nothing")
        await client.on_message(message)
        channel.sent.assert_called_with("Prices must be numbers.", None)

    async def test_on_message_graph_without_user(self, client, graph):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # first log some buy and sell prices
        await client.on_message(Message(FRIEND, channel, "!buy 100"))
        await client.on_message(Message(FRIEND, channel, "!sell 600"))
        await client.on_message(Message(BUDDY, channel, "!buy 120"))
        await client.on_message(Message(BUDDY, channel, "!sell 90"))
        await client.on_message(Message(BUDDY, channel, "!sell 200"))
        await client.on_message(Message(GUY, channel, "!sell 800"))

        author = someone()
        message = Message(author, channel, "!graph")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "__**Historical Graph for All Users**__", Matching(is_discord_file)
        )
        graph.assert_called_with(channel, None, turbot.GRAPHCMD_FILE)
        assert Path(turbot.GRAPHCMD_FILE).exists()

    async def test_on_message_graph_with_user(self, client, graph):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # first log some buy and sell prices
        await client.on_message(Message(FRIEND, channel, "!buy 100"))
        await client.on_message(Message(FRIEND, channel, "!sell 600"))
        await client.on_message(Message(BUDDY, channel, "!buy 120"))
        await client.on_message(Message(BUDDY, channel, "!sell 90"))
        await client.on_message(Message(BUDDY, channel, "!sell 200"))
        await client.on_message(Message(GUY, channel, "!sell 800"))

        author = someone()
        message = Message(author, channel, f"!graph {BUDDY.name}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"__**Historical Graph for {BUDDY}**__", Matching(is_discord_file)
        )
        graph.assert_called_with(channel, f"{BUDDY}", turbot.GRAPHCMD_FILE)
        assert Path(turbot.GRAPHCMD_FILE).exists()

    async def test_on_message_graph_with_bad_name(self, client, graph):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # first log some buy and sell prices
        await client.on_message(Message(FRIEND, channel, "!buy 100"))
        await client.on_message(Message(FRIEND, channel, "!sell 600"))
        await client.on_message(Message(BUDDY, channel, "!buy 120"))
        await client.on_message(Message(BUDDY, channel, "!sell 90"))
        await client.on_message(Message(BUDDY, channel, "!sell 200"))
        await client.on_message(Message(GUY, channel, "!sell 800"))

        author = someone()
        message = Message(author, channel, f"!graph {PUNK.name}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"Can not find the user named {PUNK.name} in this channel.", None
        )
        graph.assert_not_called()
        assert not Path(turbot.GRAPHCMD_FILE).exists()

    async def test_on_message_lastweek_none(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()
        message = Message(author, channel, "!lastweek")
        await client.on_message(message)
        channel.sent.assert_called_with("No graph from last week.", None)

    async def test_on_message_lastweek(self, client, freezer, lastweek):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        await client.on_message(Message(someone(), channel, "!reset"))
        channel.sent.assert_called_with("**Resetting data for a new week!**", None)
        lastweek.assert_called_with(channel, None, turbot.LASTWEEKCMD_FILE)
        assert Path(turbot.LASTWEEKCMD_FILE).exists()

        await client.on_message(Message(someone(), channel, "!lastweek"))
        channel.sent.assert_called_with(
            "__**Historical Graph from Last Week**__", Matching(is_discord_file)
        )

    async def test_on_message_reset(self, client, lines, freezer, lastweek):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # first log some buy and sell prices
        await client.on_message(Message(FRIEND, channel, "!buy 100"))
        await client.on_message(Message(FRIEND, channel, "!sell 600"))
        await client.on_message(Message(FRIEND, channel, "!buy 101"))
        await client.on_message(Message(FRIEND, channel, "!sell 601"))
        await client.on_message(Message(BUDDY, channel, "!buy 120"))
        await client.on_message(Message(BUDDY, channel, "!sell 90"))
        await client.on_message(Message(BUDDY, channel, "!buy 121"))
        await client.on_message(Message(BUDDY, channel, "!sell 91"))
        await client.on_message(Message(GUY, channel, "!buy 100"))
        await client.on_message(Message(GUY, channel, "!sell 800"))
        await client.on_message(Message(GUY, channel, "!buy 101"))
        await client.on_message(Message(GUY, channel, "!sell 801"))

        # then jump ahead a week and log some more
        later = NOW + timedelta(days=7)
        freezer.move_to(later)
        await client.on_message(Message(FRIEND, channel, "!buy 102"))
        await client.on_message(Message(FRIEND, channel, "!sell 602"))
        await client.on_message(Message(BUDDY, channel, "!buy 122"))
        await client.on_message(Message(BUDDY, channel, "!sell 92"))
        await client.on_message(Message(GUY, channel, "!buy 102"))
        await client.on_message(Message(GUY, channel, "!sell 802"))

        old_data = lines(client.prices_file)

        # then reset price data
        message = Message(someone(), channel, "!reset")
        await client.on_message(message)
        channel.sent.assert_called_with("**Resetting data for a new week!**", None)
        with open(client.prices_file) as f:
            assert f.readlines() == [
                "author,kind,price,timestamp\n",
                f"{FRIEND.id},buy,102,{later}\n",
                f"{BUDDY.id},buy,122,{later}\n",
                f"{GUY.id},buy,102,{later}\n",
            ]
        lastweek.assert_called_with(channel, None, turbot.LASTWEEKCMD_FILE)
        assert Path(turbot.LASTWEEKCMD_FILE).exists()

        # ensure the backup is correct
        backup_file = Path(client.last_backup_filename())
        assert backup_file.exists()
        with open(backup_file) as f:
            assert old_data == f.readlines()

    async def test_on_message_collect_no_list(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        message = Message(someone(), channel, "!collect")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Please provide the name of a fossil to mark as collected.", None
        )

    async def test_on_message_collect(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        # first collect some valid fossils
        fossils = "amber, ammonite  ,ankylo skull,amber, a foot"
        message = Message(author, channel, f"!collect {fossils}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Marked the following fossils as collected:\n"
            "> amber, ammonite, ankylo skull\n"
            "Did not recognize the following fossils:\n"
            "> a foot",
            None,
        )
        assert set(lines(client.fossils_file)) == {
            "author,name\n",
            f"{author.id},amber\n",
            f"{author.id},ankylo skull\n",
            f"{author.id},ammonite\n",
        }

        # then collect some more with dupes
        fossils = "amber,an arm,plesio body"
        message = Message(author, channel, f"!collect {fossils}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Marked the following fossils as collected:\n"
            "> plesio body\n"
            "The following fossils had already been collected:\n"
            "> amber\n"
            "Did not recognize the following fossils:\n"
            "> an arm",
            None,
        )
        assert lines(client.fossils_file) == [f"{author.id},plesio body\n"]

    async def test_on_message_fossilsearch_no_list(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        message = Message(someone(), channel, "!fossilsearch")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Please provide the name of a fossil to lookup users that don't have it.",
            None,
        )

    async def test_on_message_fossilsearch_no_need(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # first collect some valid fossils
        await client.on_message(Message(FRIEND, channel, "!collect amber, ammonite"))
        await client.on_message(Message(BUDDY, channel, "!collect amber, ammonite"))
        await client.on_message(
            Message(GUY, channel, "!collect amber, ammonite, coprolite")
        )

        # then search for things that no one needs
        message = Message(PUNK, channel, "!fossilsearch amber, ammonite")
        await client.on_message(message)
        channel.sent.assert_called_with("No one currently needs this.", None)

    async def test_on_message_fossilsearch_no_need_with_bad(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # first collect some valid fossils
        await client.on_message(Message(FRIEND, channel, "!collect amber, ammonite"))
        await client.on_message(Message(BUDDY, channel, "!collect amber, ammonite"))
        await client.on_message(
            Message(GUY, channel, "!collect amber, ammonite, coprolite")
        )

        # then search for things that no one needs
        message = Message(PUNK, channel, "!fossilsearch amber, ammonite, unicorn bits")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "__**Fossil Search**__\n"
            "> No one needs: amber, ammonite\n"
            "Did not recognize the following fossils:\n"
            "> unicorn bits",
            None,
        )

    async def test_on_message_fossilsearch(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # first collect some valid fossils
        await client.on_message(Message(FRIEND, channel, "!collect amber, ammonite"))
        await client.on_message(Message(BUDDY, channel, "!collect amber"))
        await client.on_message(Message(GUY, channel, "!collect amber, ammonite"))

        # then search for some things
        message = Message(PUNK, channel, "!fossilsearch amber, ammonite, ankylo skull")
        await client.on_message(message)
        last_call = channel.sent.call_args_list[-1][0]
        response, attachment = last_call[0], last_call[1]
        lines = response.split("\n")
        assert lines[0] == "__**Fossil Search**__"
        assert set(lines[1:]) == {
            f"> {FRIEND} needs: ankylo skull",
            f"> {BUDDY} needs: ammonite, ankylo skull",
            f"> {GUY} needs: ankylo skull",
        }
        assert not attachment

    async def test_on_message_fossilsearch_with_bad(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # first collect some valid fossils
        await client.on_message(Message(FRIEND, channel, "!collect amber, ammonite"))
        await client.on_message(Message(BUDDY, channel, "!collect amber"))
        await client.on_message(Message(GUY, channel, "!collect amber, ammonite"))

        # then search for some things
        message = Message(
            PUNK, channel, "!fossilsearch amber, ammonite, ankylo skull, unicorn bits"
        )
        await client.on_message(message)
        last_call = channel.sent.call_args_list[-1][0]
        response, attachment = last_call[0], last_call[1]
        lines = response.split("\n")
        assert lines[0] == "__**Fossil Search**__"
        assert set(lines[1:]) == {
            "Did not recognize the following fossils:",
            "> unicorn bits",
            f"> {FRIEND} needs: ankylo skull",
            f"> {BUDDY} needs: ammonite, ankylo skull",
            f"> {GUY} needs: ankylo skull",
        }
        assert not attachment

    async def test_on_message_fossilsearch_with_only_bad(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        message = Message(PUNK, channel, "!fossilsearch unicorn bits")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "__**Fossil Search**__\n"
            "Did not recognize the following fossils:\n> unicorn bits",
            None,
        )

    async def test_on_message_uncollect_no_list(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        message = Message(someone(), channel, "!uncollect")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Please provide the name of a fossil to mark as uncollected.", None
        )

    async def test_on_message_uncollect(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        # first collect some fossils
        fossils = "amber, ammonite ,ankylo skull"
        message = Message(author, channel, f"!collect {fossils}")
        await client.on_message(message)

        # then delete some of them
        fossils = "amber, a foot, coprolite, ankylo skull"
        message = Message(author, channel, f"!uncollect {fossils}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Unmarked the following fossils as collected:\n"
            "> amber, ankylo skull\n"
            "The following fossils were already marked as not collected:\n"
            "> coprolite\n"
            "Did not recognize the following fossils:\n"
            "> a foot",
            None,
        )
        assert lines(client.fossils_file) == ["author,name\n", f"{author.id},ammonite\n"]

    async def test_on_message_uncollect_with_only_bad(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        fossils = "a foot, unicorn bits"
        message = Message(author, channel, f"!uncollect {fossils}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Did not recognize the following fossils:\n> a foot, unicorn bits", None
        )

    async def test_on_message_allfossils(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        message = Message(someone(), channel, "!allfossils")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "__**All Possible Fossils**__\n"
            ">>> acanthostega, amber, ammonite, ankylo skull, ankylo tail, ankylo torso, anomalocaris, archaeopteryx, archelon skull, archelon tail, australopith, brachio chest, brachio pelvis, brachio skull, brachio tail, coprolite, deinony tail, deinony torso, dimetrodon skull, dimetrodon torso, dinosaur track, diplo chest, diplo neck, diplo pelvis, diplo skull, diplo tail, diplo tail tip, dunkleosteus, eusthenopteron, iguanodon skull, iguanodon tail, iguanodon torso, juramaia, left megalo side, left ptera wing, left quetzal wing, mammoth skull, mammoth torso, megacero skull, megacero tail, megacero torso, myllokunmingia, ophthalmo skull, ophthalmo torso, pachy skull, pachy tail, parasaur skull, parasaur tail, parasaur torso, plesio body, plesio skull, plesio tail, ptera body, quetzal torso, right megalo side, right ptera wing, right quetzal wing, sabertooth skull, sabertooth tail, shark-tooth pattern, spino skull, spino tail, spino torso, stego skull, stego tail, stego torso, t. rex skull, t. rex tail, t. rex torso, tricera skull, tricera tail, tricera torso, trilobite",  # noqa: E501
            None,
        )

    async def test_on_message_listfossils_bad_name(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        # first collect some fossils
        fossils = "amber, ammonite ,ankylo skull"
        message = Message(author, channel, f"!collect {fossils}")
        await client.on_message(message)

        # then list them
        message = Message(author, channel, f"!listfossils {PUNK.name}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"Can not find the user named {PUNK.name} in this channel.", None
        )

    async def test_on_message_listfossils_no_name(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        # first collect some fossils
        fossils = "amber, ammonite ,ankylo skull"
        message = Message(author, channel, f"!collect {fossils}")
        await client.on_message(message)

        # then list them
        message = Message(author, channel, "!listfossils")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"__**70 Fossils remaining for {author}**__\n"
            ">>> acanthostega, ankylo tail, ankylo torso, anomalocaris, "
            "archaeopteryx, archelon skull, archelon tail, australopith, brachio "
            "chest, brachio pelvis, brachio skull, brachio tail, coprolite, "
            "deinony tail, deinony torso, dimetrodon skull, dimetrodon torso, "
            "dinosaur track, diplo chest, diplo neck, diplo pelvis, diplo "
            "skull, diplo tail, diplo tail tip, dunkleosteus, eusthenopteron, "
            "iguanodon skull, iguanodon tail, iguanodon torso, juramaia, left "
            "megalo side, left ptera wing, left quetzal wing, mammoth skull, "
            "mammoth torso, megacero skull, megacero tail, megacero torso, "
            "myllokunmingia, ophthalmo skull, ophthalmo torso, pachy skull, "
            "pachy tail, parasaur skull, parasaur tail, parasaur torso, plesio "
            "body, plesio skull, plesio tail, ptera body, quetzal torso, right "
            "megalo side, right ptera wing, right quetzal wing, sabertooth skull, "
            "sabertooth tail, shark-tooth pattern, spino skull, spino tail, "
            "spino torso, stego skull, stego tail, stego torso, t. rex skull, "
            "t. rex tail, t. rex torso, tricera skull, tricera tail, tricera "
            "torso, trilobite",
            None,
        )

    async def test_on_message_listfossils_with_name(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # first have someone collect some fossils
        fossils = "amber, ammonite ,ankylo skull"
        message = Message(GUY, channel, f"!collect {fossils}")
        await client.on_message(message)

        # then have someone else list them
        message = Message(BUDDY, channel, f"!listfossils {GUY.name}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"__**70 Fossils remaining for {GUY}**__\n"
            ">>> acanthostega, ankylo tail, ankylo torso, anomalocaris, "
            "archaeopteryx, archelon skull, archelon tail, australopith, brachio "
            "chest, brachio pelvis, brachio skull, brachio tail, coprolite, "
            "deinony tail, deinony torso, dimetrodon skull, dimetrodon torso, "
            "dinosaur track, diplo chest, diplo neck, diplo pelvis, diplo "
            "skull, diplo tail, diplo tail tip, dunkleosteus, eusthenopteron, "
            "iguanodon skull, iguanodon tail, iguanodon torso, juramaia, left "
            "megalo side, left ptera wing, left quetzal wing, mammoth skull, "
            "mammoth torso, megacero skull, megacero tail, megacero torso, "
            "myllokunmingia, ophthalmo skull, ophthalmo torso, pachy skull, "
            "pachy tail, parasaur skull, parasaur tail, parasaur torso, plesio "
            "body, plesio skull, plesio tail, ptera body, quetzal torso, right "
            "megalo side, right ptera wing, right quetzal wing, sabertooth skull, "
            "sabertooth tail, shark-tooth pattern, spino skull, spino tail, "
            "spino torso, stego skull, stego tail, stego torso, t. rex skull, "
            "t. rex tail, t. rex torso, tricera skull, tricera tail, tricera "
            "torso, trilobite",
            None,
        )

    async def test_on_message_collectedfossils_no_name(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        # first collect some fossils
        fossils = "amber, ammonite ,ankylo skull"
        message = Message(author, channel, f"!collect {fossils}")
        await client.on_message(message)

        # then list them
        message = Message(author, channel, "!collectedfossils")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"__**3 Fossils donated by {author}**__\n"
            ">>> amber, ammonite, ankylo skull",
            None,
        )

    async def test_on_message_collectedfossils_with_name(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # first have someone collect some fossils
        fossils = "amber, ammonite ,ankylo skull"
        message = Message(GUY, channel, f"!collect {fossils}")
        await client.on_message(message)

        # then have someone else list them
        message = Message(BUDDY, channel, f"!collectedfossils {GUY.name}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"__**3 Fossils donated by {GUY}**__\n" ">>> amber, ammonite, ankylo skull",
            None,
        )

    async def test_on_message_fossilcount_no_params(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        message = Message(author, channel, "!fossilcount")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Please provide at least one user name to search for a fossil count.", None
        )

    async def test_on_message_fossilcount(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        # first collect some valid fossils
        await client.on_message(Message(FRIEND, channel, "!collect amber, ammonite"))
        await client.on_message(Message(BUDDY, channel, "!collect amber"))
        await client.on_message(Message(GUY, channel, "!collect amber, ammonite"))

        # then get fossil counts
        users = ", ".join([FRIEND.name, BUDDY.name, GUY.name, PUNK.name])
        message = Message(author, channel, f"!fossilcount {users}")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "__**Fossil Count**__\n"
            f"> **{BUDDY}** has 72 fossils remaining.\n"
            f"> **{FRIEND}** has 71 fossils remaining.\n"
            f"> **{GUY}** has 71 fossils remaining.\n"
            "__**Did not recognize the following names**__\n"
            f"> {PUNK.name}",
            None,
        )

    async def test_on_message_predict_no_buy(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()
        await client.on_message(Message(author, channel, "!predict"))
        channel.sent.assert_called_with(
            f"There is no recent buy price for {author}.", None
        )

    async def test_on_message_predict(self, client, freezer):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        await client.on_message(Message(author, channel, "!buy 110"))

        freezer.move_to(NOW + timedelta(days=1))
        await client.on_message(Message(author, channel, "!sell 100"))
        await client.on_message(Message(author, channel, "!sell 95"))

        freezer.move_to(NOW + timedelta(days=2))
        await client.on_message(Message(author, channel, "!sell 90"))
        await client.on_message(Message(author, channel, "!sell 85"))

        freezer.move_to(NOW + timedelta(days=4))
        await client.on_message(Message(author, channel, "!sell 90"))

        freezer.move_to(NOW + timedelta(days=5))
        await client.on_message(Message(author, channel, "!sell 120"))

        message = Message(author, channel, "!predict")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"{author}'s turnip prediction link: "
            "https://turnipprophet.io/?prices=110...100.95.90.85...90..120",
            None,
        )

    async def test_get_last_price(self, client, freezer):
        channel = Channel("text", AUTHORIZED_CHANNEL)

        # when there's no data for the user
        assert client.get_last_price(GUY) is None

        # when there's only buy data
        freezer.move_to(NOW + timedelta(days=1))
        await client.on_message(Message(GUY, channel, "!buy 102"))
        assert client.get_last_price(GUY.id) is None

        # when there's sell data for someone else
        freezer.move_to(NOW + timedelta(days=2))
        await client.on_message(Message(BUDDY, channel, "!sell 102"))
        assert client.get_last_price(GUY.id) is None

        # when there's one sell price
        freezer.move_to(NOW + timedelta(days=3))
        await client.on_message(Message(GUY, channel, "!sell 82"))
        assert client.get_last_price(GUY.id) == 82

        # when there's more than one sell price
        freezer.move_to(NOW + timedelta(days=4))
        await client.on_message(Message(GUY, channel, "!sell 45"))
        await client.on_message(Message(GUY, channel, "!sell 98"))
        assert client.get_last_price(GUY.id) == 98

    async def test_on_message_hemisphere_no_params(self, client, lines):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        message = Message(author, channel, "!hemisphere")
        await client.on_message(message)
        channel.sent.assert_called_with(
            "Please provide the name of your hemisphere, northern or southern.", None
        )

    async def test_on_message_hemisphere_bad_hemisphere(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        message = Message(author, channel, f"!hemisphere upwards")
        await client.on_message(message)
        channel.sent.assert_called_with(
            'Please provide either "northern" or "southern" as your hemisphere name.',
            None,
        )

    async def test_on_message_hemisphere(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        message = Message(author, channel, f"!hemisphere souTherN")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"Hemisphere preference registered for {author}.", None
        )
        with open(client.users_file) as f:
            assert f.readlines() == [
                "author,hemisphere\n",
                f"{author.id},Southern\n",
            ]

        message = Message(author, channel, f"!hemisphere NoRthErn")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"Hemisphere preference registered for {author}.", None
        )
        with open(client.users_file) as f:
            assert f.readlines() == [
                "author,hemisphere\n",
                f"{author.id},Northern\n",
            ]

    async def test_on_message_fish_no_hemisphere(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        message = Message(author, channel, f"!fish")
        await client.on_message(message)
        channel.sent.assert_called_with(
            f"Please enter your hemisphere choice first using the !hemisphere command.",
            None,
        )

    async def test_on_message_fish(self, client):
        channel = Channel("text", AUTHORIZED_CHANNEL)
        author = someone()

        # give our author a hemisphere first
        message = Message(author, channel, f"!hemisphere northern")
        await client.on_message(message)

        message = Message(author, channel, f"!fish")
        await client.on_message(message)
        calls = channel.sent.call_args_list

        call = calls.pop()
        response, attachment = call[0][0], call[0][1]
        assert response == (
            "> **Pale chub** is available 9 am - 4 pm at river (sells for 200 bells) \n"
            "> **Pop-eyed goldfish** is available 9 am - 4 pm at pond (sells for 1300 bells) \n"  # noqa: E501
            "> **Ranchu goldfish** is available 9 am - 4 pm at pond (sells for 4500 bells) \n"  # noqa: E501
            "> **Red snapper** is available all day at sea (sells for 3000 bells) \n"
            "> **Sea bass** is available all day at sea (sells for 400 bells) \n"
            "> **Sea horse** is available all day at sea (sells for 1100 bells) \n"
            "> **Snapping Turtle** is available 9 pm - 4 am at river (sells for 5000 bells) \n"  # noqa: E501
            "> **Squid** is available all day at sea (sells for 500 bells) \n"
            "> **Surgeonfish** is available all day at sea (sells for 1000 bells) \n"
            "> **Tadpole** is available all day at pond (sells for 100 bells) \n"
            "> **Tuna** is available all day at pier (sells for 7000 bells) **GONE NEXT MONTH!**\n"  # noqa: E501
            "> **Zebra turkeyfish** is available all day at sea (sells for 500 bells) "
        )
        assert attachment is None

        call = calls.pop()
        response, attachment = call[0][0], call[0][1]
        assert response == (
            "> **Anchovy** is available 4 am - 9 pm at sea (sells for 200 bells) \n"
            "> **Barred knifejaw** is available all day at sea (sells for 5000 bells) \n"
            "> **Barreleye** is available 9 pm - 4 am at sea (sells for 15000 bells) \n"
            "> **Black bass** is available all day at river (sells for 400 bells) \n"
            "> **Blue marlin** is available all day at pier (sells for 10000 bells) **GONE NEXT MONTH!**\n"  # noqa: E501
            "> **Bluegill** is available 9 am - 4 pm at river (sells for 180 bells) \n"
            "> **Butterfly fish** is available all day at sea (sells for 1000 bells) \n"
            "> **Carp** is available all day at pond (sells for 300 bells) \n"
            "> **Char** is available 4 pm - 9 am at river (clifftop)  pond (sells for 3800 bells) \n"  # noqa: E501
            "> **Cherry salmon** is available 4 pm - 9 am at river (clifftop) (sells for 800 bells) \n"  # noqa: E501
            "> **Clown fish** is available all day at sea (sells for 650 bells) \n"
            "> **Coelacanth** is available all day at sea (while raining) (sells for 15000 bells) \n"  # noqa: E501
            "> **Crawfish** is available all day at pond (sells for 200 bells) \n"
            "> **Crucian carp** is available all day at river (sells for 160 bells) \n"
            "> **Dab** is available all day at sea (sells for 300 bells) **GONE NEXT MONTH!**\n"  # noqa: E501
            "> **Dace** is available 4 pm - 9 am at river (sells for 240 bells) \n"
            "> **Freshwater goby** is available 4 pm - 9 am at river (sells for 400 bells) \n"  # noqa: E501
            "> **Golden trout** is available 4 pm - 9 am at river (clifftop) (sells for 15000 bells) \n"  # noqa: E501
            "> **Goldfish** is available all day at pond (sells for 1300 bells) \n"
            "> **Guppy** is available 9 am - 4 pm at river (sells for 1300 bells) \n"
            "> **Horse mackerel** is available all day at sea (sells for 150 bells) \n"
            "> **Killifish** is available all day at pond (sells for 300 bells) \n"
            "> **Koi** is available 4 pm - 9 am at pond (sells for 4000 bells) \n"
            "> **Loach** is available all day at river (sells for 400 bells) \n"
            "> **Neon tetra** is available 9 am - 4 pm at river (sells for 500 bells) \n"
            "> **Oarfish** is available all day at sea (sells for 9000 bells) \n"
            "> **Olive flounder** is available all day at sea (sells for 800 bells) "
        )
        assert attachment is None


class TestCodebase:
    def test_flake8(self):
        """Assures that the Python codebase passes configured Flake8 checks."""
        chdir(SRC_ROOT)
        proc = run(["flake8", *SRC_DIRS], capture_output=True)
        assert proc.returncode == 0, f"Flake8 issues:\n{proc.stdout.decode('utf-8')}"

    def test_black(self):
        """Assures that the Python codebase passes configured black checks."""
        chdir(SRC_ROOT)
        proc = run(["black", "--check", *SRC_DIRS], capture_output=True)
        assert proc.returncode == 0, f"black issues:\n{proc.stdout.decode('utf-8')}"

    def test_isort(self):
        """Assures that the Python codebase imports are correctly sorted."""
        chdir(SRC_ROOT)
        proc = run(["isort", "-df", "-rc", "-c", *SRC_DIRS], capture_output=True)
        assert proc.returncode == 0, f"isort issues:\n{proc.stdout.decode('utf-8')}"


class TestMeta:
    # This test will fail in isolation, you must run the full test suite
    # for it to actually pass. This is because it tracks the usage of
    # string keys over the entire test session. It can fail for two reasons:
    #
    # 1. There's a key in strings.yaml that's not being used at all.
    # 2. There's a key in strings.yaml that isn't being used in the tests.
    #
    # For situation #1 the solution is to remove the key from the config.
    # As for #2, there should be a new test which utilizes this key.
    def test_strings(self):
        """Assues that there are no missing or unused strings data."""
        used_keys = set(call[0][0] for call in S_SPY.call_args_list)
        config_keys = set(turbot.STRINGS.keys())
        assert config_keys - used_keys == set()
