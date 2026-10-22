/** One account picture load shared by the sidebar and Settings for this plugin lifetime. */
/** Owns the browser image and discards callbacks after URL replacement or plugin unload. */
export class AccountAvatarLoader {
    publish;
    snapshot;
    image;
    disposed = false;
    /** @param publish - publishes picture changes through the account snapshot. */
    constructor(publish) {
        this.publish = publish;
    }
    /**
     * Load a changed URL once; an absent or empty URL clears the picture.
     * @param url - latest successful profile's picture, absent when no picture is available.
     */
    setUrl(url) {
        if (this.disposed)
            return;
        const source = url || undefined;
        if (this.snapshot?.url === source)
            return;
        if (source === undefined) {
            this.detach();
            this.update(undefined);
        }
        else
            this.load(source);
    }
    /** Retry a failed picture once; successful and pending loads remain shared. */
    retry() {
        if (!this.disposed && this.snapshot?.status === 'failed')
            this.load(this.snapshot.url);
    }
    /**
     * Return both surfaces to fallback when a rendered picture fails.
     * @param snapshot - picture attempt rendered by the failing element; superseded attempts are ignored.
     */
    failed(snapshot) {
        if (this.disposed || this.snapshot !== snapshot || snapshot.status !== 'ready')
            return;
        this.detach();
        this.update({ url: snapshot.url, status: 'failed' });
    }
    /** Release the picture without publishing; the owner publishes an empty avatar with the replacement account frame. */
    reset() {
        this.detach();
        this.snapshot = undefined;
    }
    /** Release the browser image and prevent further loads or publications. */
    dispose() {
        this.disposed = true;
        this.reset();
    }
    load(url) {
        this.detach();
        const image = new Image();
        this.image = image;
        image.referrerPolicy = 'no-referrer';
        const settle = (status) => {
            if (this.image !== image)
                return;
            image.onload = null;
            image.onerror = null;
            this.update({ url, status });
        };
        image.onload = () => { settle('ready'); };
        image.onerror = () => { settle('failed'); };
        this.update({ url, status: 'loading' });
        if (this.image === image)
            image.src = url;
    }
    update(snapshot) {
        this.snapshot = snapshot;
        this.publish(snapshot);
    }
    detach() {
        const image = this.image;
        this.image = undefined;
        if (image === undefined)
            return;
        image.onload = null;
        image.onerror = null;
        image.removeAttribute('src');
    }
}
//# sourceMappingURL=account-avatar.js.map