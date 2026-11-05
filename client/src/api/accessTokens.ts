import { type components, GalaxyApi } from "@/api";
import { rethrowSimple } from "@/utils/simple-error";

export type AccessToken = components["schemas"]["ApiTokenModel"];
export type CreatedAccessToken = components["schemas"]["ApiTokenCreatedModel"];
export type AccessTokenCreatePayload = components["schemas"]["ApiTokenCreatePayload"];
export type AccessTokenConstraints = components["schemas"]["ApiTokenConstraints"];

/** Mirrors PRESETS in lib/galaxy/managers/api_tokens.py; the server is the source of truth. */
export const ACCESS_TOKEN_PRESETS: { value: string; text: string; description: string; scopes: string }[] = [
    {
        value: "agent",
        text: "Agent",
        description:
            "What an AI agent or MCP client needs: read everything, create histories, upload, run tools and workflows.",
        scopes: "*:read histories:write datasets:write jobs:write tools:run workflows:run invocations:write pages:write",
    },
    {
        value: "run-jobs",
        text: "Run jobs",
        description: "A service that creates histories, uploads, runs tools and fetches results. Nothing else.",
        scopes: "account:read histories:read histories:write datasets:read datasets:write tools:read tools:run jobs:read",
    },
    {
        value: "read-only",
        text: "Read-only",
        description: "Look but don't touch.",
        scopes: "*:read",
    },
    {
        value: "everything",
        text: "Everything",
        description: "All the API, like your API key, but it expires and can be revoked on its own.",
        scopes: "*:*",
    },
];

export async function fetchAccessTokens(userId: string, includeInactive = false): Promise<AccessToken[]> {
    const { data, error } = await GalaxyApi().GET("/api/users/{user_id}/tokens", {
        params: { path: { user_id: userId }, query: { include_inactive: includeInactive } },
    });
    if (error) {
        rethrowSimple(error);
    }
    return data;
}

export async function createAccessToken(
    userId: string,
    payload: AccessTokenCreatePayload,
): Promise<CreatedAccessToken> {
    const { data, error } = await GalaxyApi().POST("/api/users/{user_id}/tokens", {
        params: { path: { user_id: userId } },
        body: payload,
    });
    if (error) {
        rethrowSimple(error);
    }
    return data;
}

export async function revokeAccessToken(userId: string, tokenId: string): Promise<AccessToken> {
    const { data, error } = await GalaxyApi().DELETE("/api/users/{user_id}/tokens/{token_id}", {
        params: { path: { user_id: userId, token_id: tokenId } },
    });
    if (error) {
        rethrowSimple(error);
    }
    return data;
}
