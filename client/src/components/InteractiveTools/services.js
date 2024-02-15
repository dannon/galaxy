import axios from "axios";
import { getAppRoot } from "@/onload/loadconfig";
import { rethrowSimple } from "@/utils/simple-error";

export class Services {
    constructor(options = {}) {
        this.root = options.root || getAppRoot();
    }

    async stopInteractiveTool(id) {
        const url = `${this.root}api/entry_points/${id}`;
        const response = await axios.delete(url);
        return response.data;
    }
    catch(e) {
        rethrowSimple(e);
    }
}
