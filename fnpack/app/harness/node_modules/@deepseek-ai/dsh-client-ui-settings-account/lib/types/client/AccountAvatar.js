import { jsx as _jsx } from "react/jsx-runtime";
/** Shared account picture with an icon fallback for missing or unavailable images. */
import { IconUserOutlineMedium } from '@deepseek-ai/dsh-client-ui-primitives';
import css from './AccountAvatar.module.css';
/**
 * Render a decorative avatar next to the account identity.
 * @param props - shared browser load outcome and rendered-image failure reporter; absent data uses the default icon.
 * @returns picture or the default account icon.
 */
export function AccountAvatar({ avatar, avatarFailed }) {
    return avatar?.status === 'ready'
        ? _jsx("img", { className: css.image, src: avatar.url, alt: "", referrerPolicy: "no-referrer", onError: () => { avatarFailed(avatar); } })
        : _jsx(IconUserOutlineMedium, { size: 16 });
}
//# sourceMappingURL=AccountAvatar.js.map