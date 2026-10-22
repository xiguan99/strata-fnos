/** Keeps a preopened browser tab separate from Host authorization state. */
export class BrowserLogin {
    destination;
    popup = null;
    navigated = false;
    attempt;
    /** @param destination - maps the Host authorization URL to the page the tab opens, read when the tab navigates. */
    constructor(destination) {
        this.destination = destination;
    }
    /**
     * Preopen a tab and bind it to the attempt returned by the Host.
     * @param request - authenticated Host start request.
     * @param latest - most recent streamed state, including frames received before the response.
     * @returns after this UI is following the new attempt.
     */
    async start(request, latest) {
        this.begin();
        try {
            this.follow(await request());
            this.update(latest());
        }
        catch (error) {
            this.dispose();
            throw error;
        }
    }
    /** Preopen a tab during the user's click; blocked popups need the copy-link route. */
    begin() {
        this.dispose();
        this.popup = window.open('about:blank', '_blank');
        if (this.popup)
            this.popup.opener = null;
    }
    /**
     * Bind the preopened tab to its Host attempt.
     * @param view - initial RPC response identifying this UI's attempt.
     */
    follow(view) {
        this.attempt = view.attempt?.id;
        this.update(view);
    }
    /**
     * Navigate or close the pending tab when its attempt changes.
     * @param view - latest Host state.
     */
    update(view) {
        const attempt = view?.attempt;
        if (attempt?.id !== this.attempt)
            return;
        if (attempt && ['failed', 'expired', 'cancelled'].includes(attempt.phase)) {
            this.dispose();
            return;
        }
        if (attempt?.phase === 'succeeded') {
            // A tab that never reached the authorization page stays blank; the Host finished without it.
            if (!this.navigated)
                this.popup?.close();
            this.popup = null;
            this.attempt = undefined;
            return;
        }
        if (!attempt?.authorizeUrl || this.navigated)
            return;
        this.navigated = true;
        if (this.popup && !this.popup.closed)
            this.popup.location.replace(this.destination(attempt.authorizeUrl));
    }
    /** Close the owned authorization tab and stop following its attempt. */
    dispose() {
        this.popup?.close();
        this.popup = null;
        this.attempt = undefined;
        this.navigated = false;
    }
}
//# sourceMappingURL=browser-login.js.map