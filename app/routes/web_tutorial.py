"""Browser route for the synthetic Scribe tutorial consultation."""

from urllib.parse import urlencode

from ..main import BrowserCsrf, Depends, RedirectResponse, Request, Session, app, get_db, status
from ..main import _page_context_or_redirect
from ..services.tutorials import create_scribe_tutorial_consultation


@app.post("/transcribe/tutorial")
def create_transcribe_tutorial(
    request: Request,
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response

    transcript = create_scribe_tutorial_consultation(db, context.user)
    query = urlencode(
        {
            "transcript_id": str(transcript.id),
            "tab": "output",
            "tutorial": "1",
        }
    )
    return RedirectResponse(
        url=f"/workspace?{query}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
