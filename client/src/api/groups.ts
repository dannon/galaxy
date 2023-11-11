import axios from "axios";

import type { components } from "@/api/schema";

type GroupModel = components["schemas"]["GroupModel"];
export async function getAllGroups(): Promise<GroupModel[]> {
    const { data } = await axios.get("/api/groups");
    return data;
}
