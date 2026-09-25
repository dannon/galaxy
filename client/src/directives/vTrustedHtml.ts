/**
 * Sets `innerHTML` without cleaning it. This is the named alternative to raw
 * `v-html` for the rare content that only the Galaxy server, shipped client
 * code or the operator's config files can produce and that DOMPurify would
 * alter (see `v-safe-html` for everything else). Admin-only content entered
 * through the UI still goes through `v-safe-html`.
 *
 * Lint flags every use, so each one needs a disable directly above it that
 * says where the content comes from and why `v-safe-html` does not fit:
 *   <!-- eslint-disable-next-line vue/no-restricted-syntax -- <reason> -->
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
