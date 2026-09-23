/**
 * Sets `innerHTML` without cleaning it. This is the named alternative to raw
 * `v-html` for the rare content that only the Galaxy server or shipped client
 * code can produce and that DOMPurify would alter (see `v-safe-html` for
 * everything else).
 *
 * Every use needs an HTML comment directly above it explaining where the
 * content comes from and why `v-safe-html` does not fit, so reviewers can
 * judge the claim.
 */

import type { DirectiveBinding, ObjectDirective } from "vue";

type TrustedHtmlBinding = string | null | undefined;

export const vTrustedHtml: ObjectDirective<HTMLElement, TrustedHtmlBinding> = {
    bind(el, binding: DirectiveBinding<TrustedHtmlBinding>) {
        el.innerHTML = binding.value ?? "";
    },
    update(el, binding: DirectiveBinding<TrustedHtmlBinding>) {
        if (binding.value !== binding.oldValue) {
            el.innerHTML = binding.value ?? "";
        }
    },
};

export default vTrustedHtml;
