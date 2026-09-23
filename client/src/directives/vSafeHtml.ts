/**
 * Renders an HTML string into an element after cleaning it with DOMPurify.
 * Use this instead of `v-html` wherever markup is genuinely needed; plain
 * text belongs in `{{ }}` interpolation.
 *
 * Value forms:
 *   v-safe-html="html"                            -> `default` profile
 *   v-safe-html="{ html, profile: 'links' }"      -> named profile (see sanitizeHtml.ts)
 */

import type { DirectiveBinding, ObjectDirective } from "vue";

import { type SafeHtmlProfile, sanitizeHtml } from "./sanitizeHtml";

export type SafeHtmlBinding =
    | string
    | null
    | undefined
    | {
          html: string | null | undefined;
          profile?: SafeHtmlProfile;
      };

function normalize(value: SafeHtmlBinding): { html: string; profile: SafeHtmlProfile } {
    if (value !== null && typeof value === "object") {
        return { html: String(value.html ?? ""), profile: value.profile ?? "default" };
    }
    return { html: String(value ?? ""), profile: "default" };
}

function render(el: HTMLElement, value: SafeHtmlBinding) {
    const { html, profile } = normalize(value);
    el.innerHTML = sanitizeHtml(html, profile);
}

function isSameValue(a: SafeHtmlBinding, b: SafeHtmlBinding): boolean {
    const left = normalize(a);
    const right = normalize(b);
    return left.html === right.html && left.profile === right.profile;
}

export const vSafeHtml: ObjectDirective<HTMLElement, SafeHtmlBinding> = {
    bind(el, binding: DirectiveBinding<SafeHtmlBinding>) {
        render(el, binding.value);
    },
    update(el, binding: DirectiveBinding<SafeHtmlBinding>) {
        // Like v-html, only touch the DOM when the content changes, so code that
        // decorates the rendered nodes after mount is not undone on every re-render.
        if (!isSameValue(binding.value, binding.oldValue)) {
            render(el, binding.value);
        }
    },
    unbind(el, _binding, _vnode, _oldVnode, isDestroy?: boolean) {
        // Vue reuses an element across v-if/v-else branches; v-html clears its
        // markup in that case, so do the same. Elements being destroyed keep it
        // so leave transitions still show their content.
        if (!isDestroy) {
            el.innerHTML = "";
        }
    },
};

export default vSafeHtml;
