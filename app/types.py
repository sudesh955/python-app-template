from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.context import AppContext

    AppContextT = AppContext
else:
    AppContextT = None
