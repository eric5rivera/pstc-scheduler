import os
import re
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()


def book_pstc_interactive(headless=False):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=300 if not headless else 0)
        context = browser.new_context()
        page = context.new_page()

        try:
            print("pstc-scheduler: navigating to PSTC Scheduler...")
            page.goto("https://apps.clackamas.us/pstcscheduler/start.jsp", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            page.get_by_label("Appointment Type:").select_option("4")
            page.get_by_role("link", name="Next >").click()
            page.wait_for_timeout(3000)

            # Month Selection
            print("\nAvailable months:")
            month_dropdown = page.locator('select').nth(1)
            options = month_dropdown.locator('option').all()

            month_data = []
            for opt in options:
                name = opt.inner_text().strip()
                match = re.search(r'(\w+)\s+(\d{4})', name)
                if match:
                    month_name = match.group(1)
                    year = int(match.group(2))
                    month_num = datetime.strptime(month_name, "%B").month
                    print(f"  {month_num}. {name}")
                    month_data.append((month_num, year, opt, name))

            month_input = input("\nEnter month (name like 'June' or number like '6'): ").strip().lower()
            selected_month_num = None
            selected_year = None
            selected_opt = None

            if month_input:
                for m_num, y, opt, name in month_data:
                    if month_input in name.lower() or month_input == str(m_num):
                        selected_month_num = m_num
                        selected_year = y
                        selected_opt = opt
                        break

            if selected_opt:
                month_dropdown.select_option(index=options.index(selected_opt))
                print(f"Switching to {selected_opt.inner_text().strip()}...")
                page.wait_for_timeout(4500)

            # Determine the month currently shown in the calendar.
            # The calendar includes spillover days from adjacent months, so don't
            # filter by day number alone (e.g. "1-30"); use the selected month/year
            # and ignore cells marked as out-of-month.
            selected_month_value = month_dropdown.input_value()  # e.g. "06/2026"
            selected_month_num, selected_year = map(int, selected_month_value.split("/"))
            selected_month_key = f"{selected_month_num:02d}/{selected_year}"
            selected_month_prefix = f"{selected_month_num:02d}/"
            selected_year_suffix = f"/{selected_year}"
            print(f"Filtering to appointments in {selected_month_key}...")

            print("\nAvailable days:")
            cells = page.locator('#calendar-container td.day:not(.outmonth)').all()
            available = []

            for cell in cells:
                day_text = cell.locator('span.day').first.inner_text().strip()
                if not day_text.isdigit():
                    continue

                time_links = cell.locator('a.event-time').all()
                times = []
                for link in time_links:
                    data_value = link.get_attribute("data-value") or ""
                    date_value = data_value.split("|", 1)[0]
                    if date_value.startswith(selected_month_prefix) and date_value.endswith(selected_year_suffix):
                        time_text = link.inner_text().strip()
                        if time_text:
                            times.append(time_text)

                if times:
                    available.append((day_text, times, cell))

            if not available:
                print("No available slots found.")
                return

            for day, times, _ in available:
                weekday = datetime(selected_year, selected_month_num, int(day)).strftime("%A")
                print(f"\nDay {day} ({weekday}):")
                for t in times:
                    print(f"   {t}")

            day_input = input("\nEnter day number (e.g. 23 or 26): ").strip()
            selected = next((item for item in available if item[0] == day_input), None)

            if not selected:
                print("Day not found.")
                return

            times = selected[1]
            print("\nAvailable times:")
            time_choices = []
            for i, t in enumerate(times):
                time_match = re.match(r'(\d{1,2}):\d{2}', t)
                if not time_match:
                    continue
                hour_choice = int(time_match.group(1))
                time_choices.append((hour_choice, i, t))
                print(f"  {hour_choice}. {t}")

            selected_hour = int(input("Select time slot by start hour: ").strip())
            selected_time = next((item for item in time_choices if item[0] == selected_hour), None)

            if not selected_time:
                print("Time slot not found.")
                return

            selected[2].locator('a').nth(selected_time[1]).click()

            page.wait_for_timeout(1500)

            # Personal Info
            page.get_by_role("textbox", name="*First Name:").fill(os.getenv("PSTC_FIRST_NAME", ""))
            page.get_by_role("textbox", name="*Last Name:").fill(os.getenv("PSTC_LAST_NAME", ""))
            page.get_by_role("textbox", name="*Phone Number:").fill(os.getenv("PSTC_PHONE", ""))
            page.get_by_role("textbox", name="*Email:").fill(os.getenv("PSTC_EMAIL", ""))
            page.locator('input[name="registration_instances"]').fill(os.getenv("PSTC_PARTICIPANTS", "1"))

            page.get_by_role("link", name="Next >").click()

            for keyword in ["All shooters must bring their", "wear closed", "You must have a working"]:
                page.get_by_role("checkbox", name=re.compile(keyword, re.I)).check()

            if input("\nSubmit booking? (y/N): ").strip().lower() == "y":
                page.get_by_role("link", name="Submit").click()
                print("✅ Booking submitted!")
            else:
                print("Cancelled.")

            if not headless:
                input("\nPress Enter to close...")

        except Exception as e:
            print(f"Error: {e}")
            page.screenshot(path="pstc_error.png")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    import sys
    headless = "--headless" in sys.argv
    book_pstc_interactive(headless=headless)