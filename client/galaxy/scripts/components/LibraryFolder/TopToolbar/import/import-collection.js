import { getGalaxyInstance } from "app";
import { Toast } from "ui/toast";
import _l from "utils/localization";
import mod_library_model from "mvc/library/library-model";
import _ from "underscore";
import Backbone from "backbone";
import $ from "jquery";
import { getAppRoot } from "onload/loadConfig";
import { updateProgress } from "../delete-selected";
import LIST_CREATOR from "../../../../mvc/collection/list-collection-creator";
import PAIR_CREATOR from "../../../../mvc/collection/pair-collection-creator";
import PAIRED_CREATOR from "../../../../mvc/collection/list-of-pairs-collection-creator";
import HDCA_MODEL from "../../../../mvc/history/hdca-model";

var ImportCollectionModal = Backbone.View.extend({
    options: null,

    initialize: function (options) {
        this.options = options;
        this.options.chain_call_control = {
            total_number: 0,
            failed_number: 0,
        };
        this.showCollectionSelect();
    },

    findCheckedItems: function () {
        return this.options.selected;
    },
    // TODO find a way to import this part from another component.... or just vuefy this module!
    fetchUserHistories: function () {
        this.histories = new mod_library_model.GalaxyHistories();
        return this.histories.fetch();
    },
    showCollectionSelect: function (e) {
        const Galaxy = getGalaxyInstance();
        var checked_items = this.findCheckedItems();
        var template = this.templateCollectionSelectModal();

        var promise = this.fetchUserHistories();
        promise
            .done(() => {
                this.modal = Galaxy.modal;
                this.modal.show({
                    closing_events: true,
                    title: "Create History Collection from Datasets",
                    body: template({
                        selected_datasets: checked_items.dataset_ids.length,
                        histories: this.histories.models,
                    }),
                    buttons: {
                        Continue: () => {
                            this.showCollectionBuilder(checked_items.dataset_ids);
                        },
                        Close: () => {
                            Galaxy.modal.hide();
                        },
                    },
                });
            })
            .fail((model, response) => {
                if (typeof response.responseJSON !== "undefined") {
                    Toast.error(response.responseJSON.err_msg);
                } else {
                    Toast.error("An error occurred.");
                }
            });
    },
    /**
     * Note: The collection creation process expects ldda_ids as ids
     * in the collection_elements array but we operate on ld_ids in libraries.
     * The code below overwrites the id with ldda_id for this reason.
     */
    showCollectionBuilder: function (checked_items) {
        const Galaxy = getGalaxyInstance();
        let collection_elements = [];
        const elements_source = this.modal.$('input[type="radio"]:checked').val();
        if (elements_source === "selection") {
            for (let i = checked_items.length - 1; i >= 0; i--) {
                const collection_item = {};
                const dataset = checked_items[i];
                collection_item.id = dataset.ldda_id;
                collection_item.name = dataset.name;
                collection_item.deleted = dataset.deleted;
                collection_item.state = dataset.state;
                collection_elements.push(collection_item);
            }
        } else if (elements_source === "folder") {
            const all_datasets = Galaxy.libraries.folderListView.folder_container.get("folder").where({ type: "file" });
            collection_elements = new Backbone.Collection(all_datasets).toJSON();
            for (var i = collection_elements.length - 1; i >= 0; i--) {
                collection_elements[i].id = collection_elements[i].ldda_id;
            }
        }
        const new_history_name = this.modal.$("input[name=history_name]").val();
        if (new_history_name !== "") {
            this.createNewHistory(new_history_name)
                .done((new_history) => {
                    Toast.success("History created");
                    this.collectionImport(collection_elements, new_history.id, new_history.name);
                })
                .fail((xhr, status, error) => {
                    Toast.error("An error occurred.");
                });
        } else {
            this.select_collection_history = this.modal.$el.find("#library-collection-history-select");
            const selected_history_id = this.select_collection_history.val();
            const selected_history_name = this.select_collection_history.find("option:selected").text();
            this.collectionImport(collection_elements, selected_history_id, selected_history_name);
        }
    },
    collectionImport: function (collection_elements, history_id, history_name) {
        const modal_title = `Creating Collection in ${history_name}`;
        let creator_class;
        let creationFn;
        this.collectionType = this.modal.$el.find("#library-collection-type-select").val();
        if (this.collectionType === "list") {
            creator_class = LIST_CREATOR.ListCollectionCreator;
            creationFn = (elements, name, hideSourceItems) => {
                elements = elements.map((element) => ({
                    id: element.id,
                    name: element.name,
                    src: "ldda",
                }));
                return this.createHDCA(elements, this.collectionType, name, hideSourceItems, history_id);
            };
            LIST_CREATOR.collectionCreatorModal(
                collection_elements,
                { creationFn: creationFn, title: modal_title, defaultHideSourceItems: true },
                creator_class
            );
        } else if (this.collectionType === "paired") {
            creator_class = PAIR_CREATOR.PairCollectionCreator;
            creationFn = (elements, name, hideSourceItems) => {
                elements = [
                    { name: "forward", src: "ldda", id: elements[0].id },
                    { name: "reverse", src: "ldda", id: elements[1].id },
                ];
                return this.createHDCA(elements, this.collectionType, name, hideSourceItems, history_id);
            };
            LIST_CREATOR.collectionCreatorModal(
                collection_elements,
                { creationFn: creationFn, title: modal_title, defaultHideSourceItems: true },
                creator_class
            );
        } else if (this.collectionType === "list:paired") {
            const elements = collection_elements.map((element) => ({
                id: element.id,
                name: element.name,
                src: "ldda",
            }));
            PAIRED_CREATOR.pairedCollectionCreatorModal(elements, {
                historyId: history_id,
                title: modal_title,
                defaultHideSourceItems: true,
            });
        } else if (this.collectionType === "rules") {
            const creationFn = (elements, collectionType, name, hideSourceItems) => {
                return this.createHDCA(elements, collectionType, name, hideSourceItems, history_id);
            };
            LIST_CREATOR.ruleBasedCollectionCreatorModal(collection_elements, "library_datasets", "collections", {
                creationFn: creationFn,
                defaultHideSourceItems: true,
            });
        }
    },
    createHDCA: function (elementIdentifiers, collectionType, name, hideSourceItems, history_id, options) {
        const hdca = new HDCA_MODEL.HistoryDatasetCollection({
            history_content_type: "dataset_collection",
            collection_type: collectionType,
            history_id: history_id,
            name: name,
            hide_source_items: hideSourceItems || false,
            element_identifiers: elementIdentifiers,
        });
        return hdca.save(options);
    },
    templateCollectionSelectModal: function () {
        return _.template(
            `<div> <!-- elements selection -->
                <div class="library-modal-item">
                    <h4>Which datasets?</h4>
                    <form class="form-inline">
                        <div class="form-check form-check-inline">
                            <input class="form-check-input" type="radio" name="radio_elements" id="selection_radio" value="selection"
                                <% if (!selected_datasets) { %> disabled <% } else { %> checked <% } %> />
                            <label class="form-check-label" for="selection_radio">
                                current selection
                                <% if (selected_datasets) { %>
                                    (<%- selected_datasets %>)
                                <% } %>
                            </label>
                        </div>
                        <div class="form-check form-check-inline">
                            <input class="form-check-input" type="radio" name="radio_elements" id="folder_radio" value="folder" <% if (!selected_datasets) { %> checked <% } %> >
                            <label class="form-check-label" for="folder_radio">all datasets in current folder</label>
                        </div>
                    </form>
                </div>
                <!-- type selection -->
                <div class="library-modal-item">
                    <h4>Collection type</h4>
                    <div class="form-group">
                        <select id="library-collection-type-select" name="library-collection-type-select" class="form-control">
                            <option value="list">List</option>
                            <option value="paired">Paired</option>
                            <option value="list:paired">List of Pairs</option>
                            <option value="rules">From Rules</option>
                        </select>
                    </div>
                    <h5>Which type to choose?</h5>
                    <dl class="row">
                        <dt class="col-sm-3">List</dt>
                        <dd class="col-sm-9">Generic collection which groups any number of datasets into a set; similar to file system folder.</dd>

                        <dt class="col-sm-3">Paired</dt>
                        <dd class="col-sm-9">Simple collection containing exactly two sequence datasets; one reverse and the other forward.</dd>

                        <dt class="col-sm-3">List of Pairs</dt>
                        <dd class="col-sm-9">Advanced collection containing any number of Pairs; imagine as Pair-type collections inside of a List-type collection.</dd>

                        <dt class="col-sm-3">From Rules</dt>
                        <dd class="col-sm-9">Use Galaxy's rule builder to describe collections. This is more of an advanced feature that allows building any number of collections or any type.</dd>
                    </dl>
                </div>
                <!-- history selection/creation -->
                <div class="library-modal-item">
                    <h4>Select history</h4>
                    <div class="form-group">
                        <select id="library-collection-history-select" name="library-collection-history-select" class="form-control">
                            <% _.each(histories, function(history) { %> <!-- history select box -->
                                <option value="<%= _.escape(history.get("id")) %>">
                                    <%= _.escape(history.get("name")) %>
                                </option>
                            <% }); %>
                        </select>
                        <label>or create new:</label>
                        <input class="form-control" type="text" name="history_name" value="" placeholder="name of the new history" />
                    </div>
                </div>
            </div>`
        );
    },
});

export default {
    ImportCollectionModal: ImportCollectionModal,
};
