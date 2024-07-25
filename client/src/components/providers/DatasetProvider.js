import { fetchDatasetDetails } from "@/api/datasets";

import { SingleQueryProvider } from "./SingleQueryProvider";
import { stateIsTerminal } from "./utils";

export default SingleQueryProvider(fetchDatasetDetails, stateIsTerminal);
