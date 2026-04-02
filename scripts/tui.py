"""Terminal User Interface for PyBoty using textual."""

import argparse
import sys

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, Vertical
    from textual.widgets import Header, Footer, Static, Log, DataTable, Label
    from textual.reactive import reactive
except ImportError:
    print("Error: 'textual' is not installed. Please run: pip install textual")
    sys.exit(1)

# Note: This is a placeholder/mock TUI to demonstrate the capability.
# In a real implementation, this would connect to the PyBoty API or event bus.

class PyBotyTUI(App):
    """A Textual app to monitor PyBoty."""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2;
        grid-columns: 1fr 2fr;
    }
    #sidebar {
        width: 100%;
        height: 100%;
        border-right: solid green;
    }
    #main {
        width: 100%;
        height: 100%;
    }
    .box {
        height: 100%;
        border: solid green;
        padding: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="sidebar"):
            yield Static("System Status", classes="box")
            yield DataTable(id="status-table")
        with Container(id="main"):
            yield Static("Event Log", classes="box")
            yield Log(id="event-log")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#status-table", DataTable)
        table.add_columns("Metric", "Value")
        table.add_row("Status", "Running")
        table.add_row("Active Workflows", "0")
        table.add_row("Active Subagents", "0")
        table.add_row("Pending Approvals", "0")
        
        log = self.query_one("#event-log", Log)
        log.write_line("PyBoty TUI Initialized.")
        log.write_line("Connecting to local daemon...")
        log.write_line("Connected successfully.")

    def action_refresh(self) -> None:
        log = self.query_one("#event-log", Log)
        log.write_line("Refreshing data...")

def main():
    parser = argparse.ArgumentParser(description="PyBoty Terminal UI")
    args = parser.parse_args()
    
    app = PyBotyTUI()
    app.run()

if __name__ == "__main__":
    main()
