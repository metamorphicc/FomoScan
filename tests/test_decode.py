import unittest

from fomo_scan import (
    LAUNCHED_TOPIC,
    TRADE_TOPIC,
    decode_launched,
    decode_trade,
    topic_to_address,
)


def topic(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().removeprefix("0x")


def word(value: int) -> str:
    return f"{value:064x}"


def enc_string(value: str) -> str:
    raw = value.encode("utf-8").hex()
    padded_len = ((len(raw) + 63) // 64) * 64
    return word(len(value.encode("utf-8"))) + raw.ljust(padded_len, "0")


class DecodeTests(unittest.TestCase):
    def test_topic_to_address(self):
        self.assertEqual(
            topic_to_address(topic("0x1234567890abcdef1234567890abcdef12345678")),
            "0x1234567890abcdef1234567890abcdef12345678",
        )

    def test_decode_launched(self):
        parts = [enc_string("Test Token"), enc_string("TEST"), enc_string("desc"), enc_string("ipfs://image")]
        offsets = []
        cursor = 32 * 4
        for part in parts:
            offsets.append(cursor)
            cursor += len(part) // 2
        data = "0x" + "".join(word(offset) for offset in offsets) + "".join(parts)
        log = {
            "topics": [
                LAUNCHED_TOPIC,
                topic("0x1111111111111111111111111111111111111111"),
                topic("0x2222222222222222222222222222222222222222"),
                topic("0x3333333333333333333333333333333333333333"),
            ],
            "data": data,
            "blockNumber": "0x10",
            "transactionHash": "0xabc",
        }
        launch = decode_launched(log)
        self.assertEqual(launch.name, "Test Token")
        self.assertEqual(launch.symbol, "TEST")
        self.assertEqual(launch.description, "desc")
        self.assertEqual(launch.image_uri, "ipfs://image")
        self.assertEqual(launch.block_number, 16)

    def test_decode_trade(self):
        log = {
            "topics": [
                TRADE_TOPIC,
                topic("0x1111111111111111111111111111111111111111"),
                topic("0x2222222222222222222222222222222222222222"),
            ],
            "data": "0x" + word(1) + word(10**18) + word(123) + word(456) + word(0),
            "blockNumber": "0x20",
            "transactionHash": "0xdef",
        }
        token, trader, is_buy, eth_amount, token_amount, price, on_pool, block, tx = decode_trade(log)
        self.assertTrue(is_buy)
        self.assertFalse(on_pool)
        self.assertEqual(eth_amount, 10**18)
        self.assertEqual(token_amount, 123)
        self.assertEqual(price, 456)
        self.assertEqual(block, 32)
        self.assertEqual(tx, "0xdef")
        self.assertTrue(token.startswith("0x11"))
        self.assertTrue(trader.startswith("0x22"))


if __name__ == "__main__":
    unittest.main()
