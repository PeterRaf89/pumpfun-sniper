import asyncio
import json
import time
import base58
import aiohttp
import websockets

from solders.keypair import Keypair
from solana.rpc.async_api import AsyncClient

import config


class PumpSniper:

    def __init__(self):

        self.positions = {}
        self.bought_tokens = set()

        self.wallet = self.load_wallet()

        self.rpc = AsyncClient(config.RPC_URL)

    def load_wallet(self):

        decoded = base58.b58decode(config.PRIVATE_KEY)

        return Keypair.from_bytes(decoded)

    async def heartbeat(self):

        while True:

            print(f"[BOT] alive | positions={len(self.positions)}")

            await asyncio.sleep(config.HEARTBEAT_INTERVAL)

    async def get_price(self, session, mint):

        try:

            params = {"ids": mint}

            async with session.get(config.PRICE_API, params=params) as r:

                data = await r.json()

                if mint not in data["data"]:
                    return None

                return float(data["data"][mint]["price"])

        except Exception:

            return None

    async def get_quote(self, session, input_mint, output_mint, amount):

        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount,
            "slippageBps": 300
        }

        async with session.get(config.JUPITER_QUOTE_API, params=params) as r:

            return await r.json()

    async def buy(self, session, mint):

        if mint in self.bought_tokens:
            return

        if len(self.positions) >= config.MAX_OPEN_POSITIONS:
            return

        try:

            lamports = int(config.BUY_AMOUNT_SOL * 1_000_000_000)

            quote = await self.get_quote(
                session,
                config.SOL_MINT,
                mint,
                lamports
            )

            if "data" not in quote or len(quote["data"]) == 0:
                print("[BUY] no swap route")
                return

            price = await self.get_price(session, mint)

            if price is None:
                return

            self.positions[mint] = {
                "buy_price": price,
                "timestamp": time.time()
            }

            self.bought_tokens.add(mint)

            print(f"[BUY] {mint} at {price}")

        except Exception as e:

            print("[BUY ERROR]", e)

    async def sell(self, session, mint, reason):

        try:

            print(f"[SELL] {mint} | {reason}")

            del self.positions[mint]

        except Exception as e:

            print("[SELL ERROR]", e)

    async def monitor_positions(self):

        async with aiohttp.ClientSession() as session:

            while True:

                for mint in list(self.positions.keys()):

                    price = await self.get_price(session, mint)

                    if not price:
                        continue

                    buy_price = self.positions[mint]["buy_price"]

                    pnl = (price - buy_price) / buy_price

                    print(f"[PNL] {mint} {round(pnl*100,2)}%")

                    if pnl >= config.TAKE_PROFIT:
                        await self.sell(session, mint, "TAKE PROFIT")

                    elif pnl <= config.STOP_LOSS:
                        await self.sell(session, mint, "STOP LOSS")

                await asyncio.sleep(config.PRICE_CHECK_INTERVAL)

    async def detect_token(self, log):

        words = log.split()

        for word in words:

            if word.endswith("pump"):
                return word

        return None

    async def listen(self):

        async with aiohttp.ClientSession() as session:

            while True:

                try:

                    async with websockets.connect(config.WS_URL) as ws:

                        subscribe = {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "logsSubscribe",
                            "params": [
                                {"mentions": [config.PUMPFUN_PROGRAM]},
                                {"commitment": "confirmed"}
                            ]
                        }

                        await ws.send(json.dumps(subscribe))

                        print("[WS] listening for pump launches")

                        while True:

                            msg = await ws.recv()

                            data = json.loads(msg)

                            if "params" not in data:
                                continue

                            logs = data["params"]["result"]["value"]["logs"]

                            for log in logs:

                                mint = await self.detect_token(log)

                                if mint:

                                    print("[TOKEN DETECTED]", mint)

                                    await self.buy(session, mint)

                except Exception as e:

                    print("[WS ERROR]", e)

                    print("[WS] reconnecting in 5 seconds")

                    await asyncio.sleep(5)

    async def run(self):

        print("Wallet:", self.wallet.pubkey())

        await asyncio.gather(
            self.listen(),
            self.monitor_positions(),
            self.heartbeat()
        )


if __name__ == "__main__":

    bot = PumpSniper()

    asyncio.run(bot.run())
