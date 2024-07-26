import axios from "axios";
import beautify from "xml-beautifier";

import { getAppRoot } from "@/onload/loadConfig";
import { rethrowSimple } from "@/utils/simple-error";

import { SingleQueryProvider } from "./SingleQueryProvider";

async function toolSource({ id }) {
    const url = `${getAppRoot()}api/tools/${id}/raw_tool_source`;
    try {
        const { data, headers } = await axios.get(url);
        const result = {};
        result.language = headers.language;
        if (headers.language === "xml") {
            result.source = beautify(data);
        } else {
            result.source = data;
        }
        return result;
    } catch (e) {
        rethrowSimple(e);
    }
}

export const ToolSourceProvider = SingleQueryProvider(toolSource);
