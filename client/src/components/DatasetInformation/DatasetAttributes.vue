<template>
    <div aria-labelledby="dataset-attributes-heading">
        <h1 id="dataset-attributes-heading" v-localize class="h-lg">Edit Dataset Attributes</h1>
        <b-alert v-if="messageText" class="dataset-attributes-alert" :variant="messageVariant" show>
            {{ localized.messageText }}
        </b-alert>
        <DatasetAttributesProvider :id="datasetId" v-slot="{ result, loading }" @error="onError">
            <div v-if="!loading" class="mt-3">
                <b-tabs>
                    <b-tab v-if="!result['attribute_disable']">
                        <template v-slot:title>
                            <FontAwesomeIcon icon="bars" class="mr-1" />{{ localized.attributes }}
                        </template>
                        <FormDisplay :inputs="result['attribute_inputs']" @onChange="onAttribute" />
                        <div class="mt-2">
                            <b-button
                                id="dataset-attributes-default-save"
                                variant="primary"
                                class="mr-1"
                                @click="submit('attribute', 'attributes')">
                                <FontAwesomeIcon icon="save" class="mr-1" />{{ localized.save }}
                            </b-button>
                            <b-button v-if="!result['metadata_disable']" @click="submit('attribute', 'autodetect')">
                                <FontAwesomeIcon icon="redo" class="mr-1" />{{ localized.autoDetect }}
                            </b-button>
                        </div>
                    </b-tab>
                    <b-tab
                        v-if="
                            (!result['conversion_disable'] || !result['datatype_disable']) &&
                            !result['metadata_disable']
                        ">
                        <template v-slot:title>
                            <FontAwesomeIcon icon="database" class="mr-1" />{{ localized.datatypes }}
                        </template>
                        <div v-if="!result['datatype_disable']" class="ui-portlet-section">
                            <div class="portlet-header">
                                <div class="portlet-title">
                                    <FontAwesomeIcon icon="database" class="portlet-title-icon fa-fw mr-1" />
                                    <span class="portlet-title-text">
                                        <b itemprop="name">{{ localized.assignDatatype }}</b>
                                    </span>
                                </div>
                            </div>
                            <div class="portlet-content">
                                <FormDisplay :inputs="result['datatype_inputs']" @onChange="onDatatype" />
                                <div class="mt-2">
                                    <b-button variant="primary" class="mr-1" @click="submit('datatype', 'datatype')">
                                        <FontAwesomeIcon icon="save" class="mr-1" />{{ localized.save }}
                                    </b-button>
                                    <b-button @click="submit('datatype', 'datatype_detect')">
                                        <FontAwesomeIcon icon="redo" class="mr-1" />{{ localized.autoDetect }}
                                    </b-button>
                                </div>
                            </div>
                        </div>
                        <div v-if="!result['conversion_disable']" class="ui-portlet-section">
                            <div class="portlet-header">
                                <div class="portlet-title">
                                    <FontAwesomeIcon icon="cog" class="portlet-title-icon fa-fw mr-1" />
                                    <span class="portlet-title-text">
                                        <b itemprop="name">{{ localized.convertToDatatype }}</b>
                                    </span>
                                </div>
                            </div>
                            <div class="portlet-content">
                                <FormDisplay :inputs="result['conversion_inputs']" @onChange="onConversion" />
                                <div class="mt-2">
                                    <b-button variant="primary" @click="submit('conversion', 'conversion')">
                                        <FontAwesomeIcon icon="exchange-alt" class="mr-1" />{{
                                            localized.createDatatype
                                        }}
                                    </b-button>
                                </div>
                            </div>
                        </div>
                    </b-tab>
                    <b-tab v-if="!result['permission_disable']">
                        <template v-slot:title>
                            <FontAwesomeIcon icon="user" class="mr-1" />{{ localized.permissions }}
                        </template>
                        <FormDisplay :inputs="result['permission_inputs']" @onChange="onPermission" />
                        <div class="mt-2">
                            <b-button variant="primary" @click="submit('permission', 'permission')">
                                <FontAwesomeIcon icon="save" class="mr-1" />{{ localized.save }}
                            </b-button>
                        </div>
                    </b-tab>
                </b-tabs>
            </div>
        </DatasetAttributesProvider>
    </div>
</template>

<script>
import { library } from "@fortawesome/fontawesome-svg-core";
import { faBars, faCog, faDatabase, faExchangeAlt, faRedo, faSave, faUser } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { getGalaxyInstance } from "app";
import FormDisplay from "components/Form/FormDisplay";
import { DatasetAttributesProvider } from "components/providers/DatasetProvider";

import { localize } from "@/utils/localization";

import { setAttributes } from "./services";

library.add(faBars, faCog, faDatabase, faExchangeAlt, faRedo, faSave, faUser);

export default {
    components: {
        DatasetAttributesProvider,
        FontAwesomeIcon,
        FormDisplay,
    },
    props: {
        datasetId: {
            type: String,
            required: true,
        },
    },
    data() {
        return {
            messageText: null,
            messageVariant: null,
            formData: {},
        };
    },
    computed: {
        localized() {
            return {
                messageText: localize(this.messageText),
                attributes: localize("Attributes"),
                save: localize("Save"),
                autoDetect: localize("Auto-detect"),
                datatypes: localize("Datatypes"),
                assignDatatype: localize("Assign Datatype"),
                convertToDatatype: localize("Convert to Datatype"),
                createDataset: localize("Create Dataset"),
                permissions: localize("Permissions"),
            };
        },
    },
    methods: {
        onAttribute(data) {
            this.formData["attribute"] = data;
        },
        onConversion(data) {
            this.formData["conversion"] = data;
        },
        onDatatype(data) {
            this.formData["datatype"] = data;
        },
        onPermission(data) {
            this.formData["permission"] = data;
        },
        onError(messageText) {
            this.messageText = messageText;
            this.messageVariant = "danger";
        },
        submit(key, operation) {
            setAttributes(this.datasetId, this.formData[key], operation).then((response) => {
                this.messageText = response.message;
                this.messageVariant = response.status;
                this._reloadHistory();
            }, this.onError);
        },
        /** reload Galaxy's history after updating dataset's attributes */
        _reloadHistory: function () {
            const Galaxy = getGalaxyInstance();
            if (Galaxy) {
                Galaxy.currHistoryPanel.loadCurrentHistory();
            }
        },
    },
};
</script>
