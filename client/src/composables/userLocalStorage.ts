// userLocalStorage.ts
import { watchImmediate } from "@vueuse/core";
import { type Ref, ref, watch } from "vue";

import { type AnyUser } from "@/api";

import { useHashedUserId } from "./hashedUserId";
import { syncRefToLocalStorage } from "./persistentRef";

interface PendingChange {
    key: string;
    value: any; // Use any here
    type: "string" | "number" | "boolean" | "object";
}

const pendingChanges: PendingChange[] = [];

/**
 * Local storage composable specific to current user.
 * @param key
 * @param initialValue
 */
export function useUserLocalStorage<T>(key: string, initialValue: T, user?: Ref<AnyUser>): Ref<T> {
    //return Ref<T>
    const { hashedUserId } = useHashedUserId(user);
    const refToSync = ref(initialValue) as Ref<T>;
    let hasSynced = false;

    // Function to apply pending changes
    const applyPendingChanges = (userId: string) => {
        const changesToApply = pendingChanges.filter((change) => change.key === key);
        if (changesToApply.length > 0) {
            console.debug(`Applying ${changesToApply.length} pending changes for key "${key}"`);
            changesToApply.forEach((change) => {
                // No try-catch is needed when applying to memory (it's needed when parsing)
                // No type checking or casting is needed here!
                refToSync.value = change.value;

                //remove item from pending changes
                const index = pendingChanges.indexOf(change);
                if (index > -1) {
                    pendingChanges.splice(index, 1);
                }
            });

            //  Sync to local storage after applying in-memory changes.
            syncRefToLocalStorage(`${key}-${userId}`, refToSync);
        }
    };

    // Watch for changes to refToSync and store them if hashedUserId is not yet available.
    watch(
        refToSync,
        (newValue) => {
            if (!hashedUserId.value) {
                console.debug(`Queueing change for key "${key}" until user ID is available.`);
                // Check if a change for this key already exists, and update it, or add a new change

                const existingChangeIndex = pendingChanges.findIndex((change) => change.key === key);
                const type = typeof newValue as "string" | "number" | "boolean" | "object";
                if (existingChangeIndex > -1) {
                    pendingChanges[existingChangeIndex] = { key, value: newValue, type };
                } else {
                    pendingChanges.push({ key, value: newValue, type });
                }
            } else if (!hasSynced) {
                //This case handles calls to setItem that might happen *after* hashedUserId is set,
                // but *before* this particular watchImmediate has run.
                syncRefToLocalStorage(`${key}-${hashedUserId.value}`, refToSync);
            }
        },
        { deep: true }
    );

    watchImmediate(
        () => hashedUserId.value,
        () => {
            if (hashedUserId.value && !hasSynced) {
                applyPendingChanges(hashedUserId.value); // Apply pending changes first
                syncRefToLocalStorage(`${key}-${hashedUserId.value}`, refToSync);
                hasSynced = true;
            } else {
                console.debug("NO USER -- SKIPPING", key, hashedUserId.value);
            }
        }
    );

    return refToSync;
}
