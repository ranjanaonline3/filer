import logging
import time
from datetime import datetime
from prettytable import PrettyTable
import pyotp
from NorenRestApiPy.NorenApi import NorenApi
from concurrent.futures import ThreadPoolExecutor

# Configurations
DEFAULT_STOP_LOSS_PERCENT = 2.0
DEFAULT_TARGET_PERCENT = 5.0
FETCH_INTERVAL = 5  # Interval (in seconds) between fetches
END_TIME = "15:25"  # Trading end time


class Logger:
    """
    Centralized logging and event management.
    """
    def __init__(self):
        self.event_table = PrettyTable()
        self.event_table.field_names = ["Time", "Status", "Description"]

    def log_event(self, status, description):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.event_table.add_row([current_time, status, description])
        print(f"{current_time} - {status}: {description}")
        logging.info(f"{status}: {description}")

    def get_event_table(self):
        return self.event_table


class ShoonyaAPI:
    """
    Handles interactions with the Shoonya API.
    """
    def __init__(self, logger):
        self.logger = logger
        self.api = NorenApi(
            host="https://api.shoonya.com/NorenWClientTP/",
            websocket="wss://api.shoonya.com/NorenWS/"
        )
        self.session = None

    def login(self, userid, password, factor2, vendor_code, api_key, imei):
        self.logger.log_event("Info", "Generating OTP for login.")
        totp = pyotp.TOTP(factor2)
        otp = totp.now()
        self.logger.log_event("Info", "OTP generated successfully.")

        self.logger.log_event("Info", "Attempting to log in to Shoonya API.")
        response = self.api.login(
            userid=userid,
            password=password,
            twoFA=otp,
            vendor_code=vendor_code,
            api_secret=api_key,
            imei=imei
        )
        if response.get("stat") == "Ok":
            self.logger.log_event("Success", "Login successful.")
            self.session = response
            return True
        else:
            self.logger.log_event("Failed", f"Login failed: {response.get('emsg')}")
            return False

    def get_positions(self):
        try:
            return self.api.get_positions()
        except Exception as e:
            self.logger.log_event("Error", f"Error fetching positions: {e}")
            return []

    def get_quotes(self, tradingsymbol):
        try:
            return self.api.get_quotes(exchange="NFO", token=tradingsymbol)
        except Exception as e:
            self.logger.log_event("Error", f"Error fetching quotes for {tradingsymbol}: {e}")
            return {}

    def place_order(self, tradingsymbol, quantity, action):
        try:
            response = self.api.place_order(
                buy_or_sell=action,
                product_type="M",
                exchange="NFO",
                tradingsymbol=tradingsymbol,
                quantity=quantity,
                discloseqty=0,
                price_type="MKT",
                price=0,
                trigger_price=None,
                retention="DAY"
            )
            if response.get("stat") == "Ok":
                self.logger.log_event("Success", f"Order placed successfully: {response}")
            else:
                self.logger.log_event("Failed", f"Order placement failed: {response.get('emsg')}")
        except Exception as e:
            self.logger.log_event("Error", f"Error placing order: {e}")


class TradeMonitor:
    """
    Monitors trades and executes stop-loss/target orders.
    """
    def __init__(self, api, logger):
        self.api = api
        self.logger = logger
        self.tracked_positions = {}

    def calculate_prices(self, buy_price, stop_loss_percent, target_percent):
        stop_loss_price = buy_price * (1 - stop_loss_percent / 100)
        target_price = buy_price * (1 + target_percent / 100)
        return stop_loss_price, target_price

    def monitor_trade(self, position, stop_loss_percent, target_percent):
        tradingsymbol = position["tradingsymbol"]
        buy_price = position["avgnetprice"]
        quantity = position["netqty"]

        stop_loss_price, target_price = self.calculate_prices(buy_price, stop_loss_percent, target_percent)
        self.logger.log_event("Info", f"Monitoring {tradingsymbol} with quantity {quantity}, "
                                      f"buy price {buy_price}, stop-loss {stop_loss_price}, "
                                      f"and target {target_price}.")

        while True:
            response = self.api.get_quotes(tradingsymbol)
            if response.get("stat") != "Ok":
                self.logger.log_event("Error", f"Error fetching quotes: {response.get('emsg')}")
                time.sleep(5)
                continue

            current_price = float(response.get("lp", 0))
            if current_price >= target_price:
                self.logger.log_event("Success", f"Target hit for {tradingsymbol}. Placing sell order.")
                self.api.place_order(tradingsymbol, quantity, "S")
                break
            elif current_price <= stop_loss_price:
                self.logger.log_event("Success", f"Stop-loss hit for {tradingsymbol}. Placing sell order.")
                self.api.place_order(tradingsymbol, quantity, "S")
                break

            self.logger.log_event("Info", f"{tradingsymbol} within bounds. Current: {current_price}, "
                                          f"Stop-loss: {stop_loss_price}, Target: {target_price}.")
            time.sleep(5)


class TradingBot:
    """
    Orchestrates the trading bot.
    """
    def __init__(self):
        self.logger = Logger()
        self.api = ShoonyaAPI(self.logger)
        self.monitor = TradeMonitor(self.api, self.logger)
        self.executor = ThreadPoolExecutor()

    def run(self):
        self.logger.log_event("Info", "Starting the trading bot.")
        if not self.api.login(
            userid="FA93204",
            password="Sri@39993",
            factor2="RMBE3QH237D363E27YYY4R7CXCD5L66N",
            vendor_code="FA93204_U",
            api_key="534f0be9804ce406a05805f13d3cde89",
            imei="abc1234"
        ):
            self.logger.log_event("Error", "Exiting due to failed login.")
            return

        while datetime.now().time() < datetime.strptime(END_TIME, "%H:%M").time():
            positions = self.api.get_positions()
            for position in positions:
                if position["tradingsymbol"] not in self.monitor.tracked_positions:
                    self.logger.log_event("Info", f"New position detected: {position['tradingsymbol']}.")
                    self.monitor.tracked_positions[position["tradingsymbol"]] = position
                    self.executor.submit(
                        self.monitor.monitor_trade,
                        position,
                        DEFAULT_STOP_LOSS_PERCENT,
                        DEFAULT_TARGET_PERCENT
                    )
            self.logger.log_event("Info", "Waiting before the next position fetch.")
            time.sleep(FETCH_INTERVAL)

        self.logger.log_event("Info", "Shutting down trading bot.")
        self.api.api.logout()


if __name__ == "__main__":
    bot = TradingBot()
    bot.run()