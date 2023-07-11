import createClient from "openapi-fetch";

import { getAppRoot } from "@/onload/loadConfig";

import { paths } from "./schema"; // generated from openapi-typescript

export const { get, put } = createClient<paths>({ baseUrl: getAppRoot(undefined, true) });

// // Type-checked request
// await put("/blogposts", {
//   body: {
//     title: "My New Post",
//     // ❌ Property 'publish_date' is missing in type …
//   },
// });

// // Type-checked response
// const { data, error } = await get("/blogposts/{post_id}", { params: { path: { post_id: "123" } } });
