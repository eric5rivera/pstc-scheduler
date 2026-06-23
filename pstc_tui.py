import asyncio
import os
import re
from urllib.parse import urljoin
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

load_dotenv()


PROFILE_PATH = Path.home() / ".pstc-scheduler" / "profile.yaml"
PROFILE_FIELDS = {
    "first_name": ("PSTC_FIRST_NAME", ""),
    "last_name": ("PSTC_LAST_NAME", ""),
    "phone": ("PSTC_PHONE", ""),
    "email": ("PSTC_EMAIL", ""),
    "participants": ("PSTC_PARTICIPANTS", "1"),
}

PREFERENCE_FIELDS = {
    "startup_mode": "browse",  # browse | auto_pick
    "default_weekday": "3",    # Monday=0, Thursday=3
    "default_hour": "6",
    "default_period": "PM",
}

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class MonthOption:
    month_num: int
    year: int
    value: str
    name: str
    index: int


@dataclass
class TimeOption:
    hour_choice: int
    text: str
    data_value: str


@dataclass
class DayOption:
    day: int
    weekday: str
    times: list[TimeOption]


class PSTCTui(App):
    TITLE = "pstc-scheduler"
    SUB_TITLE = "PSTC appointment scheduler"
    theme = "gruvbox"

    CSS = """
    Screen {
        layout: vertical;
    }

    #statusbar {
        height: 1;
        background: $surface;
    }

    #status {
        width: 1fr;
        padding: 0 1;
    }

    #main {
        height: 1fr;
    }

    .panel {
        width: 1fr;
        border: solid $primary;
        padding: 1;
    }

    .panel-title {
        text-style: bold;
        margin-bottom: 1;
    }

    ListView {
        height: 1fr;
    }

    #details {
        height: 3;
        padding: 0 1;
        border: solid $secondary;
    }

    #form {
        display: none;
        height: auto;
        padding: 0 1;
        border: solid $secondary;
    }

    #booking_summary {
        margin: 0 0 1 0;
        padding: 0 1;
        border: solid $accent;
        background: $panel;
        color: $accent;
        text-style: bold;
    }

    .field-row {
        height: 3;
    }

    .field-label {
        width: 18;
        content-align: right middle;
        padding-right: 1;
    }

    #form_actions {
        margin-top: 1;
    }

    Input {
        margin-bottom: 0;
        width: 1fr;
    }

    Button {
        margin-right: 2;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
    ]

    LIST_IDS = ["months", "days", "times"]

    def __init__(self, headless: bool = True, startup_mode: str | None = None):
        super().__init__()
        self.headless = headless
        self.startup_mode_override = startup_mode
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

        self.months: list[MonthOption] = []
        self.days: list[DayOption] = []
        self.selected_month: MonthOption | None = None
        self.selected_day: DayOption | None = None
        self.selected_time: TimeOption | None = None
        self.cancellation_url: str | None = None
        self.pending_month_index: int | None = None
        self.desired_month_index: int | None = None
        self.focus_days_after_month_load = False
        self.force_focus_days_for_month_index: int | None = None
        self.init_task: asyncio.Task | None = None
        self.month_load_task: asyncio.Task | None = None
        self.prefetch_task: asyncio.Task | None = None
        self.month_cache: dict[str, list[DayOption]] = {}
        self.suppress_month_highlight = False
        self.suppress_day_highlight = False
        self.preview_token = 0
        self.month_render_token = 0
        self.number_buffer = ""
        self.number_buffer_task: asyncio.Task | None = None
        self.background_tasks: set[asyncio.Task] = set()
        self.previous_loop_exception_handler = None

        self.booking_profile = self.load_booking_profile()
        self.default_appointment_date = self.next_weekday_date(self.default_weekday())
        self.default_appointment_hour = self.default_hour()
        self.default_appointment_period = self.default_period()
        self.loading = True

    def load_booking_profile(self) -> dict[str, str]:
        try:
            with PROFILE_PATH.open() as profile_file:
                profile = yaml.safe_load(profile_file) or {}
        except (FileNotFoundError, yaml.YAMLError, OSError):
            return {}

        if not isinstance(profile, dict):
            return {}

        allowed_keys = set(PROFILE_FIELDS) | set(PREFERENCE_FIELDS)
        return {key: str(value) for key, value in profile.items() if key in allowed_keys}

    def profile_value(self, key: str) -> str:
        env_name, default = PROFILE_FIELDS[key]
        return os.getenv(env_name, self.booking_profile.get(key, default))

    def preference_value(self, key: str) -> str:
        env_name = f"PSTC_{key.upper()}"
        return os.getenv(env_name, self.booking_profile.get(key, PREFERENCE_FIELDS[key]))

    def startup_mode(self) -> str:
        mode = self.startup_mode_override or self.preference_value("startup_mode")
        return mode if mode in {"browse", "auto_pick"} else "browse"

    def default_weekday(self) -> int:
        value = self.preference_value("default_weekday").strip()
        if value.isdigit() and 0 <= int(value) <= 6:
            return int(value)
        for index, name in enumerate(WEEKDAY_NAMES):
            if value.lower() == name.lower():
                return index
        return 3

    def default_hour(self) -> int:
        try:
            hour = int(self.preference_value("default_hour"))
            return hour if 1 <= hour <= 12 else 6
        except ValueError:
            return 6

    def default_period(self) -> str:
        period = self.preference_value("default_period").upper()
        return period if period in {"AM", "PM"} else "PM"

    def refresh_default_appointment(self) -> None:
        self.default_appointment_date = self.next_weekday_date(self.default_weekday())
        self.default_appointment_hour = self.default_hour()
        self.default_appointment_period = self.default_period()

    def save_booking_profile(self) -> None:
        try:
            profile = {
                key: self.query_one(f"#{key}", Input).value
                for key in PROFILE_FIELDS
            }
            for key in PREFERENCE_FIELDS:
                profile[key] = self.booking_profile.get(key, PREFERENCE_FIELDS[key])
            PROFILE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temp_path = PROFILE_PATH.with_suffix(".yaml.tmp")
            with temp_path.open("w") as profile_file:
                yaml.safe_dump(profile, profile_file, sort_keys=False)
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, PROFILE_PATH)
            os.chmod(PROFILE_PATH, 0o600)
            self.booking_profile = profile
        except Exception:
            # Saving the local convenience profile should never interrupt booking.
            pass

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="statusbar"):
            yield Static("Starting browser...", id="status")
        with Horizontal(id="main"):
            with Vertical(classes="panel"):
                yield Label("Available Months", classes="panel-title")
                yield ListView(id="months")
            with Vertical(classes="panel"):
                yield Label("Available Days", classes="panel-title")
                yield ListView(id="days")
            with Vertical(classes="panel"):
                yield Label("Available Times", classes="panel-title")
                yield ListView(id="times")
        yield Static("Select a month to begin.", id="details")
        with Vertical(id="form"):
            yield Label("Booking Info")
            yield Static("", id="booking_summary")
            with Horizontal(classes="field-row"):
                yield Label("First Name", classes="field-label")
                yield Input(value=self.profile_value("first_name"), placeholder="First name", id="first_name")
            with Horizontal(classes="field-row"):
                yield Label("Last Name", classes="field-label")
                yield Input(value=self.profile_value("last_name"), placeholder="Last name", id="last_name")
            with Horizontal(classes="field-row"):
                yield Label("Phone", classes="field-label")
                yield Input(value=self.profile_value("phone"), placeholder="Phone", id="phone")
            with Horizontal(classes="field-row"):
                yield Label("Email", classes="field-label")
                yield Input(value=self.profile_value("email"), placeholder="Email", id="email")
            with Horizontal(classes="field-row"):
                yield Label("Shooters", classes="field-label")
                yield Input(value=self.profile_value("participants"), placeholder="Participants", id="participants")
            with Horizontal(id="form_actions"):
                yield Button("Submit Booking (Enter/s)", id="submit", variant="success", disabled=True)
                yield Button("Back to Times (b/Esc)", id="cancel", variant="error")
                yield Button("Cancel Appointment (x)", id="cancel_appointment", variant="warning")
        yield Footer()

    async def on_mount(self) -> None:
        loop = asyncio.get_running_loop()
        self.previous_loop_exception_handler = loop.get_exception_handler()
        loop.set_exception_handler(self.handle_loop_exception)
        self.set_status("pstc-scheduler is starting browser and fetching availability in the background...")
        self.set_details("Loading pstc-scheduler... The interface is ready; availability will appear shortly.")
        self.query_one("#cancel_appointment", Button).display = False
        self.init_task = self.create_background_task(self.initialize_scheduler())

    async def on_unmount(self) -> None:
        tasks = [self.init_task, self.month_load_task, self.prefetch_task, self.number_buffer_task, *self.background_tasks]
        active_tasks = [task for task in tasks if task and not task.done()]
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        self.save_booking_profile()
        await self.cleanup()

    def handle_loop_exception(self, loop, context) -> None:
        exception = context.get("exception")
        if exception and exception.__class__.__name__ == "TargetClosedError":
            return

        if self.previous_loop_exception_handler:
            self.previous_loop_exception_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    def create_background_task(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)
        task.add_done_callback(self.discard_background_task)
        return task

    def discard_background_task(self, task: asyncio.Task) -> None:
        self.background_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.exception()
        except Exception:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in PROFILE_FIELDS:
            self.save_booking_profile()

    def describe_default_appointment(self) -> str:
        weekday = WEEKDAY_NAMES[self.default_weekday()]
        return f"next {weekday} at {self.default_hour()} {self.default_period()}"

    def toggle_startup_behavior(self) -> None:
        if self.startup_mode() == "auto_pick":
            self.booking_profile["startup_mode"] = "browse"
            self.save_booking_profile()
            self.set_status("Startup behavior saved: browse availability first.")
            self.set_details("Next launch will start by showing available appointments instead of auto-picking a usual slot.")
            return

        if self.selected_month and self.selected_day and self.selected_time:
            selected_date = datetime(self.selected_month.year, self.selected_month.month_num, self.selected_day.day).date()
            self.booking_profile["default_weekday"] = str(selected_date.weekday())
            self.booking_profile["default_hour"] = str(self.selected_time.hour_choice)
            self.booking_profile["default_period"] = "PM" if "PM" in self.selected_time.text.upper() else "AM"

        self.booking_profile["startup_mode"] = "auto_pick"
        self.refresh_default_appointment()
        self.save_booking_profile()
        self.set_status(f"Startup behavior saved: auto-pick {self.describe_default_appointment()}.")
        self.set_details(
            "Next launch will try to open the booking form for your usual appointment. "
            "Press 'a' again any time to switch back to browsing first."
        )

    async def on_key(self, event) -> None:
        # Vim-style navigation for the three appointment lists.
        form_visible = bool(self.query_one("#form").display)

        # The booking form behaves like a panel to the right of the time list:
        # right/l opens it from a focused time, left/Esc goes back to times.
        # Left/Esc work even while editing a booking field.
        if event.key in {"escape", "left"} and form_visible:
            await self.cancel_booking_form()
        elif event.key == "enter" and form_visible and not self.query_one("#submit", Button).disabled:
            await self.submit_booking()
        elif event.key == "h" and form_visible and not isinstance(self.focused, Input):
            await self.cancel_booking_form()
        # Avoid intercepting normal typing while editing booking form inputs.
        elif isinstance(self.focused, Input):
            return
        elif event.key.isdigit() and isinstance(self.focused, ListView):
            await self.handle_number_shortcut(event.key)
        elif event.key == "s" and form_visible and not self.query_one("#submit", Button).disabled:
            await self.submit_booking()
        elif event.key == "x" and form_visible and self.query_one("#cancel_appointment", Button).display:
            await self.cancel_submitted_appointment()
        elif event.key == "a":
            self.toggle_startup_behavior()
        elif event.key in {"b", "c"} and form_visible:
            await self.cancel_booking_form()
        elif event.key == "j":
            self.move_list_cursor(1)
        elif event.key == "k":
            self.move_list_cursor(-1)
        elif event.key in {"h", "left"}:
            self.focus_adjacent_list(-1)
        elif event.key in {"l", "right"}:
            if isinstance(self.focused, ListView) and self.focused.id == "times" and self.focused.index is not None:
                await self.select_time(self.focused.index)
            else:
                self.focus_adjacent_list(1)
        elif event.key == "enter" and isinstance(self.focused, ListView):
            await self.select_focused_list_item(advance_focus=True)
        elif event.key == "space" and isinstance(self.focused, ListView):
            self.focused.action_select_cursor()
        else:
            return

        event.prevent_default()
        event.stop()

    async def select_focused_list_item(self, advance_focus: bool) -> None:
        if not isinstance(self.focused, ListView) or self.focused.index is None:
            return

        index = self.focused.index
        if self.focused.id == "months":
            cached_days = self.month_cache.get(self.month_cache_key(self.months[index]))
            self.queue_month_load(index, focus_days_after_load=advance_focus, show_cached=False)
            if advance_focus:
                if cached_days is not None:
                    self.force_focus_days_for_month_index = index
                    await self.show_month_days(index, cached_days, from_cache=True)
                else:
                    await self.show_days_loading_placeholder(self.months[index])
                self.focus_days_panel()
        elif self.focused.id == "days":
            if advance_focus:
                await self.select_day(index)
            else:
                await self.preview_day(index)
        elif self.focused.id == "times":
            await self.select_time(index)

    async def handle_number_shortcut(self, digit: str) -> None:
        # Months are special: available months are shown by calendar number
        # (6, 7, 8, etc.), and choosing one should behave like selecting it and
        # immediately moving right into the Days pane. Do this before using the
        # generic delayed number buffer used for multi-digit days/times.
        if isinstance(self.focused, ListView) and self.focused.id == "months":
            if self.number_buffer_task and not self.number_buffer_task.done():
                self.number_buffer_task.cancel()
            self.number_buffer = ""

            month_values = [str(month.month_num) for month in self.months]
            exact_matches = [index for index, value in enumerate(month_values) if value == digit]
            prefix_matches = [index for index, value in enumerate(month_values) if value.startswith(digit)]
            match_index = exact_matches[0] if exact_matches else (prefix_matches[0] if len(prefix_matches) == 1 else None)

            if match_index is not None:
                self.query_one("#months", ListView).index = match_index
                cached_days = self.month_cache.get(self.month_cache_key(self.months[match_index]))
                self.queue_month_load(match_index, focus_days_after_load=True, show_cached=False)
                if cached_days is not None:
                    self.force_focus_days_for_month_index = match_index
                    await self.show_month_days(match_index, cached_days, from_cache=True)
                else:
                    await self.show_days_loading_placeholder(self.months[match_index])
                self.focus_days_panel()
                return

        self.number_buffer += digit

        if self.number_buffer_task and not self.number_buffer_task.done():
            self.number_buffer_task.cancel()

        await self.preview_number_shortcut()
        self.number_buffer_task = self.create_background_task(self.commit_number_shortcut_after_delay())

    def focus_days_panel(self) -> None:
        days = self.query_one("#days", ListView)
        days.focus()
        # Some ListView index/highlight/cache-render messages are processed
        # after the key event. Re-assert focus a few times so the month pane
        # can't steal it back while the selected month is still fetching.
        self.call_after_refresh(days.focus)
        self.create_background_task(self.refocus_days_soon())

    async def refocus_days_soon(self) -> None:
        for delay in (0, 0.05, 0.2):
            await asyncio.sleep(delay)
            if not self.query_one("#form").display:
                self.query_one("#days", ListView).focus()

    async def show_days_loading_placeholder(self, month: MonthOption) -> None:
        self.days = []
        self.selected_day = None
        self.selected_time = None
        day_list = self.query_one("#days", ListView)
        time_list = self.query_one("#times", ListView)
        self.suppress_day_highlight = True
        try:
            await day_list.clear()
            await time_list.clear()
            await day_list.append(ListItem(Label(f"Loading {month.name}...")))
            day_list.index = 0
        finally:
            self.suppress_day_highlight = False
        self.set_details(f"Loading {month.name} availability...")

    async def preview_number_shortcut(self) -> None:
        match_index = self.find_number_shortcut_match(self.number_buffer)
        if match_index is not None and isinstance(self.focused, ListView):
            self.focused.index = match_index

    async def commit_number_shortcut_after_delay(self) -> None:
        try:
            await asyncio.sleep(0.55)
            match_index = self.find_number_shortcut_match(self.number_buffer)
            self.number_buffer = ""
            if match_index is None or not isinstance(self.focused, ListView):
                return

            if self.focused.id == "months":
                self.queue_month_load(match_index, focus_days_after_load=False)
                await self.show_days_loading_placeholder(self.months[match_index])
                self.focus_days_panel()
            elif self.focused.id == "days":
                await self.select_day(match_index)
            elif self.focused.id == "times":
                await self.select_time(match_index)
        except asyncio.CancelledError:
            raise

    def find_number_shortcut_match(self, typed: str) -> int | None:
        if not typed or not isinstance(self.focused, ListView):
            return None

        focused_id = self.focused.id
        if focused_id == "months":
            values = [str(month.month_num) for month in self.months]
        elif focused_id == "days":
            values = [str(day.day) for day in self.days]
        elif focused_id == "times" and self.selected_day:
            values = [str(time.hour_choice) for time in self.selected_day.times]
        else:
            return None

        exact_matches = [index for index, value in enumerate(values) if value == typed]
        if exact_matches:
            return exact_matches[0]

        prefix_matches = [index for index, value in enumerate(values) if value.startswith(typed)]
        if len(prefix_matches) == 1:
            return prefix_matches[0]

        return None

    def move_list_cursor(self, direction: int) -> None:
        if not isinstance(self.focused, ListView):
            return

        if direction > 0:
            self.focused.action_cursor_down()
        else:
            self.focused.action_cursor_up()

    def focus_adjacent_list(self, direction: int) -> None:
        focused_id = self.focused.id if self.focused else None
        if focused_id not in self.LIST_IDS:
            self.query_one("#months", ListView).focus()
            return

        current_index = self.LIST_IDS.index(focused_id)
        next_index = current_index + direction
        if next_index < 0 or next_index >= len(self.LIST_IDS):
            return

        next_id = self.LIST_IDS[next_index]
        self.query_one(f"#{next_id}", ListView).focus()

    def set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    def start_loading(self, message: str) -> None:
        self.loading = True
        self.set_status(message)

    def stop_loading(self, message: str) -> None:
        self.loading = False
        self.set_status(message)

    def set_details(self, message: str) -> None:
        self.query_one("#details", Static).update(message)

    async def initialize_scheduler(self) -> None:
        try:
            self.start_loading("pstc-scheduler is opening the PSTC scheduler...")
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=self.headless, slow_mo=300 if not self.headless else 0)
            self.context = await self.browser.new_context()
            self.page = await self.context.new_page()

            await self.page.goto("https://apps.clackamas.us/pstcscheduler/start.jsp", wait_until="domcontentloaded")
            await self.page.wait_for_timeout(1500)

            self.start_loading("Selecting shooting range appointment type...")
            await self.page.get_by_label("Appointment Type:").select_option("4")
            await self.page.get_by_role("link", name="Next >").click()
            await self.page.wait_for_timeout(3000)

            await self.load_months()

            auto_pick_defaults = self.startup_mode() == "auto_pick"
            self.refresh_default_appointment()
            default_month_index = (
                self.month_index_for_date(self.default_appointment_date)
                if auto_pick_defaults
                else self.current_month_index()
            )
            if default_month_index is None:
                default_month_index = self.current_month_index()

            if default_month_index is not None:
                month_list = self.query_one("#months", ListView)
                self.suppress_month_highlight = True
                try:
                    month_list.index = default_month_index
                    month_list.focus()
                    await self.select_month(default_month_index, auto_pick_defaults=auto_pick_defaults)
                    await asyncio.sleep(0)
                finally:
                    self.suppress_month_highlight = False
            else:
                self.stop_loading("Choose a month/day/time. Vim keys: h/l panels, j/k move, Enter select, q quit.")

            self.prefetch_task = self.create_background_task(self.prefetch_all_months())
        except Exception as exc:
            self.loading = False
            self.set_status(f"Error: {exc}")
            if self.page:
                await self.page.screenshot(path="pstc_error.png")

    def next_weekday_date(self, weekday: int):
        today = datetime.now().date()
        days_ahead = (weekday - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return today + timedelta(days=days_ahead)

    def current_month_index(self) -> int | None:
        return self.month_index_for_date(datetime.now().date())

    def month_index_for_date(self, target_date) -> int | None:
        for index, month in enumerate(self.months):
            if month.month_num == target_date.month and month.year == target_date.year:
                return index
        return None

    async def load_months(self) -> None:
        month_dropdown = self.page.locator("#frm-month")
        options = await month_dropdown.locator("option").all()

        self.months = []
        month_list = self.query_one("#months", ListView)
        await month_list.clear()

        for idx, opt in enumerate(options):
            name = (await opt.inner_text()).strip()
            value = await opt.get_attribute("value") or ""
            match = re.search(r"(\w+)\s+(\d{4})", name)
            if not match:
                continue
            month_name = match.group(1)
            year = int(match.group(2))
            month_num = datetime.strptime(month_name, "%B").month
            self.months.append(MonthOption(month_num, year, value, name, idx))
            await month_list.append(ListItem(Label(f"{month_num}. {name}")))

    async def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.index is None:
            return

        if event.list_view.id == "months":
            if not self.suppress_month_highlight:
                self.queue_month_load(event.list_view.index)
        elif event.list_view.id == "days":
            # Ignore delayed/day highlight events while the booking form is open.
            # On startup, the default flow selects next Thursday at 6 PM and
            # opens the booking form; Textual may still deliver an older day
            # highlight event afterward, which was overwriting the review banner
            # with "Previewing..." text.
            if not self.suppress_day_highlight and not self.query_one("#form").display:
                await self.preview_day(event.list_view.index)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "months":
            # Clicking/selecting a month should load/preview it, but should not
            # force focus into the Days pane. Numeric month shortcuts still move
            # focus explicitly via handle_number_shortcut().
            self.queue_month_load(event.list_view.index or 0)
        elif event.list_view.id == "days":
            # Clicking/selecting a day should update the times preview only.
            # Use l/right to move into Times, or numeric day shortcuts to select
            # a day and advance focus.
            await self.preview_day(event.list_view.index or 0)
        elif event.list_view.id == "times":
            await self.select_time(event.list_view.index or 0)

    def queue_month_load(self, index: int, focus_days_after_load: bool = False, show_cached: bool = True) -> None:
        if index >= len(self.months):
            return

        self.desired_month_index = index
        self.pending_month_index = index
        self.focus_days_after_month_load = focus_days_after_load
        if focus_days_after_load:
            self.force_focus_days_for_month_index = index

        cached_days = self.month_cache.get(self.month_cache_key(self.months[index]))
        if show_cached and cached_days is not None:
            # Show cached availability immediately for snappy navigation. The
            # queue below still refreshes this month from the site in the
            # background and updates the cache/UI when it finishes.
            self.create_background_task(self.show_month_days(index, cached_days, from_cache=True))

        if not self.month_load_task or self.month_load_task.done():
            self.month_load_task = self.create_background_task(self.process_month_load_queue())

    async def process_month_load_queue(self) -> None:
        while self.pending_month_index is not None:
            index = self.pending_month_index
            focus_days = self.focus_days_after_month_load
            self.pending_month_index = None
            self.focus_days_after_month_load = False
            await self.select_month(index, focus_after_load=focus_days)

    async def select_month(self, index: int, auto_pick_defaults: bool = False, focus_after_load: bool = True) -> None:
        if index >= len(self.months):
            return

        self.desired_month_index = index
        month = self.months[index]
        cached_days = self.month_cache.get(self.month_cache_key(month))

        if cached_days is not None:
            await self.show_month_days(index, cached_days, from_cache=True)
        else:
            self.selected_month = month
            self.selected_day = None
            self.selected_time = None
            self.query_one("#submit", Button).disabled = True
            self.query_one("#form").display = False
            await self.query_one("#days", ListView).clear()
            await self.query_one("#times", ListView).clear()

        if not self.query_one("#form").display:
            self.start_loading(f"Refreshing {month.name} availability...")
        else:
            self.loading = True
        await self.change_calendar_month(month)
        fresh_days = await self.fetch_days(month)
        self.month_cache[self.month_cache_key(month)] = fresh_days

        # If the user moved to another month while this one was refreshing,
        # keep the cache update but don't overwrite the visible month.
        if self.desired_month_index != index:
            return

        await self.show_month_days(index, fresh_days, from_cache=False)

        if auto_pick_defaults:
            await self.select_default_day_and_time()
            if self.selected_time:
                self.stop_loading(f"Default selected: {self.describe_default_appointment()}. Review, then submit.")
            else:
                if focus_after_load:
                    self.query_one("#days", ListView).focus()
                self.stop_loading(f"Loaded {month.name}. Default {self.describe_default_appointment()} was not available; choose manually.")
        else:
            # If the user picked a time and opened the booking form while this
            # refresh was still in-flight, don't overwrite the booking-page
            # status with a stale "Loaded month / choose day" message.
            if self.query_one("#form").display:
                self.loading = False
                return

            if focus_after_load:
                self.query_one("#days", ListView).focus()
            self.stop_loading(f"Loaded {month.name}. Choose an available day.")

    async def change_calendar_month(self, month: MonthOption, page=None) -> None:
        page = page or self.page
        month_dropdown = page.locator("#frm-month")
        current_value = await month_dropdown.input_value()
        old_calendar = await page.locator("#calendar-container").inner_html()

        await month_dropdown.select_option(index=month.index)

        if current_value != month.value:
            # Month changes are rendered by client-side JS. Previously we used a
            # fixed sleep, which could parse the old calendar before the DOM
            # changed. That made the first click show no days, then the second
            # click worked because the calendar had finally updated.
            await page.wait_for_function(
                "oldHtml => document.querySelector('#calendar-container')?.innerHTML !== oldHtml",
                arg=old_calendar,
                timeout=10000,
            )

        await page.wait_for_selector("#calendar-container td.day", timeout=10000)
        await page.wait_for_timeout(500)

    def month_cache_key(self, month: MonthOption) -> str:
        return f"{month.year}-{month.month_num:02d}"

    async def fetch_days(self, month: MonthOption, page=None) -> list[DayOption]:
        page = page or self.page
        # Retry briefly in case the appointment links are added just after the
        # calendar table itself is swapped in.
        days: list[DayOption] = []
        for attempt in range(3):
            days = await self.parse_available_days(month, page=page)
            if days or attempt == 2:
                break
            await page.wait_for_timeout(1000)
        return days

    async def show_month_days(self, index: int, days: list[DayOption], from_cache: bool) -> None:
        if index >= len(self.months) or self.desired_month_index != index:
            return

        # Don't let a background refresh disrupt the review/booking form.
        if self.query_one("#form").display:
            return

        self.month_render_token += 1
        render_token = self.month_render_token

        month = self.months[index]
        previous_focus_id = self.focused.id if self.focused else None
        previous_day_num = self.selected_day.day if self.selected_day else None
        previous_time_text = None
        time_list = self.query_one("#times", ListView)
        if self.selected_time:
            previous_time_text = self.selected_time.text
        elif self.selected_day:
            time_index = time_list.index
            if time_index is None and time_list.highlighted_child is not None:
                try:
                    time_index = list(time_list.children).index(time_list.highlighted_child)
                except ValueError:
                    time_index = None
            if time_index is not None and time_index < len(self.selected_day.times):
                previous_time_text = self.selected_day.times[time_index].text

        self.suppress_day_highlight = True
        try:
            self.selected_month = month
            self.days = self.dedupe_days(days)
            self.selected_day = None
            self.selected_time = None
            self.query_one("#submit", Button).disabled = True
            self.query_one("#form").display = False

            day_list = self.query_one("#days", ListView)
            await day_list.clear()
            if render_token != self.month_render_token or self.desired_month_index != index:
                return

            await time_list.clear()
            if render_token != self.month_render_token or self.desired_month_index != index:
                return

            for day_option in self.days:
                await day_list.append(ListItem(Label(f"{day_option.day} ({day_option.weekday})")))
                if render_token != self.month_render_token or self.desired_month_index != index:
                    return

            if self.days:
                day_index = next((i for i, day in enumerate(self.days) if day.day == previous_day_num), 0)
                day_list.index = day_index
                await self.preview_day(day_index, force=True)
                if render_token != self.month_render_token or self.desired_month_index != index:
                    return

                if previous_time_text and self.selected_day:
                    time_index = next(
                        (i for i, time in enumerate(self.selected_day.times) if time.text == previous_time_text),
                        None,
                    )
                    if time_index is not None:
                        time_list.index = time_index

                if self.force_focus_days_for_month_index == index:
                    self.query_one("#days", ListView).focus()
                    self.force_focus_days_for_month_index = None
                elif previous_focus_id in self.LIST_IDS:
                    self.query_one(f"#{previous_focus_id}", ListView).focus()

                await asyncio.sleep(0)
                if render_token != self.month_render_token or self.desired_month_index != index:
                    return
        finally:
            if render_token == self.month_render_token:
                self.suppress_day_highlight = False

        cache_note = "cached; refreshing in background" if from_cache else "fresh"
        if not self.days:
            self.set_details(f"No available slots found for {month.name} ({cache_note}).")
        else:
            self.set_details(f"{month.name}: {len(self.days)} available day(s) ({cache_note}).")

    def dedupe_days(self, days: list[DayOption]) -> list[DayOption]:
        deduped: dict[int, DayOption] = {}
        for day in days:
            existing = deduped.get(day.day)
            if existing is None:
                deduped[day.day] = day
                continue

            seen_times = {time.text for time in existing.times}
            for time in day.times:
                if time.text not in seen_times:
                    existing.times.append(time)
                    seen_times.add(time.text)

        return [deduped[day] for day in sorted(deduped)]

    async def parse_available_days(self, month: MonthOption, page=None) -> list[DayOption]:
        page = page or self.page
        selected_month_prefix = f"{month.month_num:02d}/"
        selected_year_suffix = f"/{month.year}"
        days: list[DayOption] = []

        cells = await page.locator("#calendar-container td.day:not(.outmonth)").all()
        for cell in cells:
            day_text = (await cell.locator("span.day").first.inner_text()).strip()
            if not day_text.isdigit():
                continue

            times: list[TimeOption] = []
            seen_time_values: set[str] = set()
            seen_time_texts: set[str] = set()
            links = await cell.locator("a.event-time").all()
            for link in links:
                data_value = await link.get_attribute("data-value") or ""
                if data_value in seen_time_values:
                    continue
                seen_time_values.add(data_value)
                date_value = data_value.split("|", 1)[0]
                if not (date_value.startswith(selected_month_prefix) and date_value.endswith(selected_year_suffix)):
                    continue

                time_text = (await link.inner_text()).strip()
                if time_text in seen_time_texts:
                    continue
                seen_time_texts.add(time_text)
                time_match = re.match(r"(\d{1,2}):\d{2}", time_text)
                if time_text and time_match:
                    times.append(TimeOption(int(time_match.group(1)), time_text, data_value))

            if times:
                day = int(day_text)
                weekday = datetime(month.year, month.month_num, day).strftime("%A")
                days.append(DayOption(day, weekday, times))

        return self.dedupe_days(days)

    async def prefetch_all_months(self) -> None:
        """Warm the month cache on a separate hidden page.

        This keeps startup interactive: the default month loads first on the
        main page, then the remaining months are fetched in the background so
        later month switches can render instantly from cache.
        """
        context = None
        try:
            if not self.browser:
                return

            context = await self.browser.new_context()
            page = await context.new_page()
            await page.goto("https://apps.clackamas.us/pstcscheduler/start.jsp", wait_until="domcontentloaded")
            await page.wait_for_timeout(1000)
            await page.get_by_label("Appointment Type:").select_option("4")
            await page.get_by_role("link", name="Next >").click()
            await page.wait_for_timeout(2500)

            for index, month in enumerate(self.months):
                key = self.month_cache_key(month)
                if key in self.month_cache:
                    continue

                if not self.query_one("#form").display:
                    self.set_status(f"Background caching {month.name}...")
                await self.change_calendar_month(month, page=page)
                days = await self.fetch_days(month, page=page)
                self.month_cache[key] = days

                if self.desired_month_index == index:
                    await self.show_month_days(index, days, from_cache=False)

            if self.selected_month and not self.query_one("#form").display:
                self.set_status(f"Ready. Cached {len(self.month_cache)} month(s). Vim keys: h/l panels, j/k move, Enter select, q quit.")
        except asyncio.CancelledError:
            raise
        except Exception:
            # Background caching should never interrupt normal booking flow.
            pass
        finally:
            if context:
                await context.close()

    async def select_default_day_and_time(self) -> None:
        if not self.selected_month:
            return

        target = self.default_appointment_date
        if self.selected_month.month_num != target.month or self.selected_month.year != target.year:
            return

        day_index = next((i for i, day in enumerate(self.days) if day.day == target.day), None)
        if day_index is None:
            self.set_details(
                f"Default target is {self.describe_default_appointment()}, {target.strftime('%B')} {target.day}, "
                f"but that day has no available appointments."
            )
            return

        day_list = self.query_one("#days", ListView)
        self.suppress_day_highlight = True
        day_list.index = day_index
        self.suppress_day_highlight = False
        await self.select_day(day_index)

        assert self.selected_day is not None
        time_index = next(
            (
                i for i, time in enumerate(self.selected_day.times)
                if time.hour_choice == self.default_appointment_hour and self.default_appointment_period in time.text.upper()
            ),
            None,
        )
        if time_index is None:
            self.set_details(
                f"{self.selected_day.weekday}, {target.strftime('%B')} {target.day} is available, "
                f"but {self.default_appointment_hour} {self.default_appointment_period} is not. Choose another time."
            )
            return

        time_list = self.query_one("#times", ListView)
        time_list.index = time_index
        time_list.focus()
        await self.select_time(time_index)

    async def preview_day(self, index: int, force: bool = False) -> None:
        if index >= len(self.days) or (self.query_one("#form").display and not force):
            return

        day = self.days[index]
        month = self.selected_month
        if month is None:
            return

        self.preview_token += 1
        token = self.preview_token
        self.selected_day = day
        self.selected_time = None
        self.query_one("#submit", Button).disabled = True
        self.query_one("#form").display = False

        await self.populate_times(day, token)
        if token != self.preview_token:
            return

        self.set_details(
            f"Previewing: {day.weekday}, "
            f"{month.name.split()[0]} {day.day}, {month.year}\n"
            "Available times are shown on the right."
        )

    async def populate_times(self, day: DayOption, token: int) -> None:
        time_list = self.query_one("#times", ListView)
        await time_list.clear()
        if token != self.preview_token:
            return

        seen: set[str] = set()
        for time in day.times:
            if token != self.preview_token:
                return
            if time.text in seen:
                continue
            seen.add(time.text)
            await time_list.append(ListItem(Label(f"{time.hour_choice}. {time.text}")))
        if day.times and token == self.preview_token:
            time_list.index = 0

    async def select_day(self, index: int) -> None:
        if index >= len(self.days):
            return

        await self.preview_day(index)
        self.query_one("#times", ListView).focus()

        self.set_details(
            f"Selected: {self.selected_day.weekday}, "
            f"{self.selected_month.name.split()[0]} {self.selected_day.day}, {self.selected_month.year}\n"
            "Now choose a time."
        )
        self.set_status("Choose an available time. j/k move, Enter/l/→ book, h/← back.")

    async def select_time(self, index: int) -> None:
        if not self.selected_day or index >= len(self.selected_day.times):
            return

        # Invalidate any in-flight day preview so it can't overwrite the
        # booking-review banner after the form opens.
        self.preview_token += 1
        self.selected_time = self.selected_day.times[index]
        self.query_one("#booking_summary", Static).update(
            f"APPOINTMENT: {self.selected_day.weekday}, "
            f"{self.selected_month.name.split()[0]} {self.selected_day.day}, {self.selected_month.year} "
            f"• {self.selected_time.text}"
        )
        self.query_one("#form").display = True
        self.query_one("#submit", Button).disabled = False
        self.set_details("Ready to book. Review the booking info below, then submit or go back to choose another time.")
        self.set_status("Review booking info. Submit: Enter or s. Back to times: ←, h, b, c, or Esc.")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            await self.cancel_booking_form()
        elif event.button.id == "submit":
            await self.submit_booking()
        elif event.button.id == "cancel_appointment":
            await self.cancel_submitted_appointment()

    async def cancel_booking_form(self) -> None:
        self.save_booking_profile()
        day = self.selected_day
        previous_time = self.selected_time
        self.selected_time = None
        self.cancellation_url = None
        self.query_one("#cancel_appointment", Button).display = False
        self.query_one("#booking_summary", Static).update("")
        self.query_one("#form").display = False
        self.query_one("#submit", Button).disabled = True

        if day:
            # Rebuild the time list when leaving the review form. On startup the
            # default booking flow can open the form before the times panel has
            # painted, so simply hiding the form could reveal an empty times
            # column.
            self.preview_token += 1
            await self.populate_times(day, self.preview_token)
            if previous_time:
                time_index = next(
                    (
                        i for i, time in enumerate(day.times)
                        if time.data_value == previous_time.data_value or time.text == previous_time.text
                    ),
                    None,
                )
                if time_index is not None:
                    self.query_one("#times", ListView).index = time_index
            self.set_details(
                f"Selected: {day.weekday}, "
                f"{self.selected_month.name.split()[0]} {day.day}, {self.selected_month.year}\n"
                "Booking cancelled. Choose another time, or press q to quit."
            )
            self.query_one("#times", ListView).focus()
            self.set_status("Choose an available time. j/k move, Enter/l/→ book, h/← back.")
        else:
            self.set_details("Booking cancelled. Choose a month/day/time, or press q to quit.")
            self.set_status("Choose a month/day/time. Vim keys: h/l panels, j/k move, Enter select, q quit.")

    async def submit_booking(self) -> None:
        self.save_booking_profile()
        if not (self.selected_month and self.selected_day and self.selected_time):
            self.set_status("Choose a month, day, and time first.")
            return

        try:
            self.start_loading("Submitting booking details...")
            await self.page.locator(f'a.event-time[data-value="{self.selected_time.data_value}"]').click()
            await self.page.wait_for_timeout(1500)

            await self.page.get_by_role("textbox", name="*First Name:").fill(self.query_one("#first_name", Input).value)
            await self.page.get_by_role("textbox", name="*Last Name:").fill(self.query_one("#last_name", Input).value)
            await self.page.get_by_role("textbox", name="*Phone Number:").fill(self.query_one("#phone", Input).value)
            await self.page.get_by_role("textbox", name="*Email:").fill(self.query_one("#email", Input).value)
            await self.page.locator('input[name="registration_instances"]').fill(self.query_one("#participants", Input).value)

            await self.page.get_by_role("link", name="Next >").click()
            await self.page.wait_for_timeout(1000)

            web_error = await self.get_web_error_message()
            if web_error:
                await self.handle_reservation_error(web_error)
                return

            for keyword in ["All shooters must bring their", "wear closed", "You must have a working"]:
                await self.page.get_by_role("checkbox", name=re.compile(keyword, re.I)).check()

            await self.page.get_by_role("link", name="Submit").click()
            await self.page.wait_for_timeout(1500)

            self.cancellation_url = await self.find_cancellation_url()
            if self.cancellation_url:
                self.query_one("#cancel_appointment", Button).display = True
                self.stop_loading("✅ Booking submitted! Cancellation link found.")
                self.set_details("Booking submitted. Use Cancel Appointment (x) if you need to cancel it, or press q to quit.")
            else:
                self.stop_loading("✅ Booking submitted!")
                self.set_details("Booking submitted. No cancellation link was detected on the page. You may press q to quit.")
            self.query_one("#submit", Button).disabled = True
        except Exception as exc:
            self.loading = False
            self.set_status(f"Error submitting booking: {exc}")
            if self.page:
                await self.page.screenshot(path="pstc_error.png")

    async def find_cancellation_url(self) -> str | None:
        links = await self.page.locator("a").all()
        for link in links:
            if not await link.is_visible():
                continue

            text = " ".join((await link.inner_text()).split()).lower()
            href = await link.get_attribute("href") or ""
            if "cancel" in text or "cancel" in href.lower():
                return urljoin(self.page.url, href) if href else self.page.url

        return None

    async def cancel_submitted_appointment(self) -> None:
        if not self.cancellation_url:
            self.set_status("No cancellation link is available for this booking.")
            return

        self.start_loading("Cancelling appointment...")
        self.query_one("#cancel_appointment", Button).disabled = True

        try:
            await self.page.goto(self.cancellation_url, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(1000)

            cancel_targets = [
                self.page.get_by_role("button", name=re.compile(r"cancel", re.I)),
                self.page.get_by_role("link", name=re.compile(r"cancel", re.I)),
                self.page.locator('input[type="submit"][value*="Cancel" i]'),
                self.page.locator('input[type="button"][value*="Cancel" i]'),
            ]

            clicked = False
            for target in cancel_targets:
                if await target.count() > 0:
                    await target.first.click()
                    clicked = True
                    break

            if not clicked:
                self.loading = False
                self.query_one("#cancel_appointment", Button).disabled = False
                self.set_status("Cancellation page loaded, but no cancel button/link was found.")
                self.set_details("Could not find a cancel control on the cancellation page. Use --show-browser to inspect the site.")
                return

            success_text = "Successfully cancelled the appointment."
            try:
                await self.page.get_by_text(success_text).wait_for(timeout=10000)
                self.cancellation_url = None
                self.query_one("#cancel_appointment", Button).display = False
                self.stop_loading("✅ Appointment cancelled.")
                self.set_details(success_text)
            except Exception:
                web_error = await self.get_web_error_message()
                self.loading = False
                self.query_one("#cancel_appointment", Button).disabled = False
                if web_error:
                    self.set_status("Cancellation may have failed; the site returned an error.")
                    self.set_details(f"The PSTC site returned an error:\n{web_error}")
                else:
                    self.set_status("Cancellation submitted, but success message was not detected.")
                    self.set_details("Expected success text was not found: Successfully cancelled the appointment.")
        except Exception as exc:
            self.loading = False
            self.query_one("#cancel_appointment", Button).disabled = False
            self.set_status(f"Error cancelling appointment: {exc}")
            if self.page:
                await self.page.screenshot(path="pstc_error.png")

    async def get_web_error_message(self) -> str | None:
        """Return any visible error/warning message from the PSTC web app.

        Known examples include:
        - The appointment time you have chosen has either already been taken...
        - There are not enough slots available for the appointment time...

        The site may add new messages later, so this also checks common
        Bootstrap/form error containers and passes the site's own text through
        to the TUI.
        """
        known_errors = [
            "The appointment time you have chosen has either already been taken or could not be reserved, please choose another.",
            "There are not enough slots available for the appointment time you have chosen, please choose another appointment time or a different number of slots.",
        ]

        for message in known_errors:
            if await self.page.get_by_text(message).count() > 0:
                return message

        error_selectors = [
            ".alert-danger",
            ".alert-warning",
            ".alert.alert-danger",
            ".alert.alert-warning",
            ".invalid-feedback",
            ".validation-summary-errors",
            "[role='alert']",
        ]
        for selector in error_selectors:
            elements = await self.page.locator(selector).all()
            for element in elements:
                if not await element.is_visible():
                    continue
                text = " ".join((await element.inner_text()).split())
                if text:
                    return text

        return None

    async def handle_reservation_error(self, web_error: str) -> None:
        """Recover when the site rejects the selected time/person count.

        The PSTC site can get stuck on the personal-info page after this error;
        changing the shooter count and pressing Next again may continue to fail.
        The reliable recovery is to go back to availability, refresh the month,
        and make the user pick from the current list again.
        """
        rejected_month = self.selected_month
        rejected_day = self.selected_day
        rejected_time = self.selected_time

        self.loading = False
        self.query_one("#booking_summary", Static).update("")
        self.query_one("#form").display = False
        self.query_one("#submit", Button).disabled = True
        self.selected_time = None

        self.set_status("Reservation failed; returning to availability and refreshing times...")
        self.set_details(
            "The PSTC site returned an error:\n"
            f"{web_error}\n"
            "Refreshing availability now; please choose a time again."
        )

        try:
            await self.page.go_back(wait_until="domcontentloaded", timeout=10000)
            await self.page.wait_for_selector("#calendar-container td.day", timeout=10000)
        except Exception:
            try:
                await self.page.get_by_role("link", name=re.compile(r"back|previous|<", re.I)).click()
                await self.page.wait_for_selector("#calendar-container td.day", timeout=10000)
            except Exception:
                self.set_status("Could not automatically return to availability. Restart the TUI or use --show-browser to inspect.")
                return

        if rejected_month:
            await self.change_calendar_month(rejected_month)
            fresh_days = await self.fetch_days(rejected_month)
            self.month_cache[self.month_cache_key(rejected_month)] = fresh_days

            month_index = self.months.index(rejected_month)
            self.desired_month_index = month_index
            await self.show_month_days(month_index, fresh_days, from_cache=False)

            if rejected_day:
                day_index = next((i for i, day in enumerate(self.days) if day.day == rejected_day.day), None)
                if day_index is not None:
                    self.query_one("#days", ListView).index = day_index
                    await self.preview_day(day_index)

        rejected = ""
        if rejected_day and rejected_time and rejected_month:
            rejected = f" Rejected slot was {rejected_day.weekday}, {rejected_month.name.split()[0]} {rejected_day.day} at {rejected_time.text}."
        self.set_status("Availability refreshed after site error. Choose another time or lower the shooter count." + rejected)

    async def cleanup(self) -> None:
        for resource in (self.context, self.browser, self.playwright):
            if not resource:
                continue
            try:
                if resource is self.playwright:
                    await resource.stop()
                else:
                    await resource.close()
            except Exception as exc:
                if exc.__class__.__name__ != "TargetClosedError":
                    raise

        self.context = None
        self.browser = None
        self.playwright = None


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="PSTC appointment scheduler TUI")
    parser.add_argument("--show-browser", action="store_true", help="Show the browser while the TUI runs")
    startup_group = parser.add_mutually_exclusive_group()
    startup_group.add_argument("--browse", action="store_true", help="Start by browsing availability, ignoring saved auto-pick preference")
    startup_group.add_argument("--auto-pick", action="store_true", help="Start by auto-picking the saved usual appointment")
    args = parser.parse_args()

    startup_mode = "auto_pick" if args.auto_pick else "browse" if args.browse else None
    PSTCTui(headless=not args.show_browser, startup_mode=startup_mode).run()


if __name__ == "__main__":
    main()
