# Spec vetting report

551 tools OK, 0 failures, 0 not discoverable by exact-name search.

| family | ok | failed |
|---|---|---|
| access | 39 | 0 |
| accesstoken | 2 | 0 |
| assets | 2 | 0 |
| attachments | 5 | 0 |
| bas | 3 | 0 |
| billing | 2 | 0 |
| billingv1 | 3 | 0 |
| crossconnects | 3 | 0 |
| diloa | 14 | 0 |
| fabric | 274 | 0 |
| getprojects | 1 | 0 |
| internetaccess | 27 | 0 |
| internetaccessv2 | 6 | 0 |
| lookup | 5 | 0 |
| network-edge | 54 | 0 |
| notifications | 4 | 0 |
| orderhistory | 2 | 0 |
| orders | 5 | 0 |
| quotes | 1 | 0 |
| reports | 13 | 0 |
| securecabinet | 2 | 0 |
| shipments | 4 | 0 |
| shipmentsv2 | 2 | 0 |
| smarthands | 14 | 0 |
| smartview | 33 | 0 |
| sts | 10 | 0 |
| support | 7 | 0 |
| supportplans | 1 | 0 |
| tickets | 5 | 0 |
| troubleticket | 3 | 0 |
| unifiednotification | 1 | 0 |
| workvisit | 4 | 0 |

## POST tools marked read-only by name

These POST operations have a read verb in their operationId or a `/search` or `/validate` path. Tools an overlay marks as writes are labelled *write (overlay)*.

- `accesstoken_GetOAuth2AccessToken` `/oauth2/v1/token` — write (overlay)
- `assets_searchAssets` `/v1/assets/search`
- `bas_searchBillingAccount` `/billing/v2/billingAccounts/search`
- `diloa_search` `/diloa/v1/digitalLoas/search`
- `fabric_searchAsns` `/fabric/v4/asns/search`
- `fabric_searchCloudEvents` `/fabric/v4/cloudevents/search`
- `fabric_searchLastOpEvents` `/fabric/v4/liveEvents/search`
- `fabric_searchMetrics` `/fabric/v4/metrics/search`
- `fabric_searchConnectionAdvertisedRoutes` `/fabric/v4/connections/{connectionId}/advertisedRoutes/search`
- `fabric_searchConnectionReceivedRoutes` `/fabric/v4/connections/{connectionId}/receivedRoutes/search`
- `fabric_searchExchangeService` `/fabric/v4/exchangeServices/search`
- `fabric_searchEiaServices` `/fabric/v4/internetAccessServices/search`
- `fabric_searchIpBlocks` `/fabric/v4/ipBlocks/search`
- `fabric_searchConnections` `/fabric/v4/connections/search`
- `fabric_searchPrices` `/fabric/v4/prices/search`
- `fabric_searchServiceProfiles` `/fabric/v4/serviceProfiles/search`
- `fabric_searchServiceTokens` `/fabric/v4/serviceTokens/search`
- `fabric_searchOpticalConnect` `/fabric/v4/opticalConnects/search`
- `fabric_searchPorts` `/fabric/v4/ports/search`
- `fabric_searchRouteFilters` `/fabric/v4/routeFilters/search`
- `fabric_searchRouteFilterRules` `/fabric/v4/routeFilters/{routeFilterId}/routeFilterRules/search`
- `fabric_searchRouteAggregations` `/fabric/v4/routeAggregations/search`
- `fabric_searchRouteAggregationRules` `/fabric/v4/routeAggregations/{routeAggregationId}/routeAggregationRules/search`
- `fabric_searchRouterActions` `/fabric/v4/routers/{routerId}/actions/search`
- `fabric_searchCloudRouterCommands` `/fabric/v4/routers/{routerId}/commands/search`
- `fabric_searchCloudRouterRoutes` `/fabric/v4/routers/{routerId}/routes/search`
- `fabric_validateRoutingProtocol` `/fabric/v4/routers/{routerId}/validate`
- `fabric_searchCloudRouterRouteFilterAttachments` `/fabric/v4/routers/{routerId}/routeFilters/search`
- `fabric_searchCloudRouterRouteAggregationAttachments` `/fabric/v4/routers/{routerId}/routeAggregations/search`
- `fabric_searchCloudRouters` `/fabric/v4/routers/search`
- `fabric_validateConnections` `/fabric/v4/connections/validate`
- `fabric_searchNetworks` `/fabric/v4/networks/search`
- `fabric_searchTimeServices` `/fabric/v4/timeServices/search`
- `fabric_searchStreams` `/fabric/v4/streams/search`
- `fabric_searchStreamAssets` `/fabric/v4/streamAssets/search`
- `fabric_searchStreamSubscriptions` `/fabric/v4/streamSubscriptions/search`
- `fabric_searchCompanyProfile` `/fabric/v4/companyProfiles/search`
- `fabric_searchLoa` `/fabric/v4/loas/search`
- `fabric_searchLoaAction` `/fabric/v4/loas/{loaId}/actions/search`
- `fabric_searchCrossConnects` `/fabric/v4/crossConnects/search`
- `fabric_searchCrossConnectAction` `/fabric/v4/crossConnects/{crossConnectId}/actions/search`
- `fabric_searchCrossConnectSchedules` `/fabric/v4/crossConnectSchedules/search`
- `fabric_searchAppDomains` `/fabric/v4/appDomains/search`
- `fabric_searchAppLinks` `/fabric/v4/appLinks/search`
- `fabric_searchAttachedAppDomains` `/fabric/v4/appLinks/{appLinkId}/appDomains/search`
- `fabric_searchAttachedAppServices` `/fabric/v4/appLinks/{appLinkId}/appServices/search`
- `fabric_searchAttachedAppProcessors` `/fabric/v4/appLinks/{appLinkId}/appProcessors/search`
- `fabric_searchAppProcessors` `/fabric/v4/appProcessors/search`
- `fabric_searchAppServices` `/fabric/v4/appServices/search`
- `fabric_searchAttachedAppSubscriptionsByAppServiceId` `/fabric/v4/appServices/{appServiceId}/appSubscriptions/search`
- `fabric_searchAppSubscriptions` `/fabric/v4/appSubscriptions/search`
- `internetaccess_searchPrices` `/internetAccess/v1/prices/search`
- `internetaccess_getEquinixInternetAccessServices` `/internetAccess/v1/services/search`
- `internetaccessv2_getEquinixInternetAccessServices` `/internetAccess/v2/services/search`
- `network-edge_getDownloadImageByDeviceType` `/ne/v1/devices/{deviceType}/repositories/{version}/download`
- `smartview_getAlerts` `/smartview/v2/systemAlerts/search`
- `sts_listAccessPoliciesGranted` `/v1/accessPoliciesGranted`
- `unifiednotification_getNotifications` `/notifications/v2/events/findAll`
