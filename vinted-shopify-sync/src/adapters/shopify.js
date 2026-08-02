/**
 * Shopify adapter — talks to the Admin GraphQL API.
 *
 * Requests are made from the service worker, where `host_permissions` exempt us
 * from CORS. Authentication is an Admin API access token from a *custom app*
 * created in the store's admin (Settings > Apps > Develop apps), with at least:
 *   read_products, write_products, read_inventory, write_inventory
 *
 * That token is store-owner-grade. See README "Security" — it is held in
 * chrome.storage.local on this machine only and never sent anywhere else.
 */

import { makeItem, normaliseText } from '../lib/item.js';
import { logger } from '../lib/logger.js';

const SIZE_OPTIONS = ['size', 'uk size', 'eu size', 'dress size'];
const COLOUR_OPTIONS = ['colour', 'color'];

export class ShopifyAdapter {
  constructor(settings) {
    this.settings = settings.shopify;
    this.currency = 'GBP';
  }

  get configured() {
    return Boolean(this.settings.shopDomain && this.settings.accessToken);
  }

  get endpoint() {
    const domain = this.settings.shopDomain.replace(/^https?:\/\//, '').replace(/\/$/, '');
    return `https://${domain}/admin/api/${this.settings.apiVersion}/graphql.json`;
  }

  async graphql(query, variables = {}) {
    if (!this.configured) throw new Error('Shopify is not configured (shop domain + access token).');

    const response = await fetch(this.endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Shopify-Access-Token': this.settings.accessToken,
      },
      body: JSON.stringify({ query, variables }),
    });

    if (response.status === 401 || response.status === 403) {
      throw new Error('Shopify rejected the access token (401/403). Check the custom app scopes.');
    }
    if (response.status === 429) {
      throw new Error('Shopify rate limit hit (429). Try again in a moment.');
    }
    if (!response.ok) {
      throw new Error(`Shopify HTTP ${response.status}: ${(await response.text()).slice(0, 300)}`);
    }

    const payload = await response.json();
    if (payload.errors?.length) {
      throw new Error(`Shopify GraphQL: ${payload.errors.map((e) => e.message).join('; ')}`);
    }
    return payload.data;
  }

  /** Also caches the shop currency and picks a default inventory location. */
  async testConnection() {
    const data = await this.graphql(`
      query {
        shop { name myshopifyDomain currencyCode }
        locations(first: 5) { nodes { id name isActive } }
      }
    `);
    this.currency = data.shop.currencyCode ?? this.currency;
    const location = data.locations.nodes.find((l) => l.isActive) ?? data.locations.nodes[0] ?? null;
    return {
      ok: true,
      shop: data.shop.name,
      domain: data.shop.myshopifyDomain,
      currency: this.currency,
      locations: data.locations.nodes,
      suggestedLocationId: location?.id ?? '',
    };
  }

  /** Walks every product/variant and flattens to one normalised item per variant. */
  async fetchItems({ pageSize = 50, maxPages = 40 } = {}) {
    const query = `
      query($cursor: String, $pageSize: Int!) {
        products(first: $pageSize, after: $cursor, sortKey: UPDATED_AT) {
          pageInfo { hasNextPage endCursor }
          nodes {
            id title handle descriptionHtml status vendor productType updatedAt onlineStoreUrl
            images(first: 10) { nodes { url } }
            variants(first: 25) {
              nodes {
                id sku price inventoryQuantity
                selectedOptions { name value }
                inventoryItem { id }
              }
            }
          }
        }
      }
    `;

    const items = [];
    let cursor = null;
    for (let page = 0; page < maxPages; page += 1) {
      const data = await this.graphql(query, { cursor, pageSize });
      const { nodes, pageInfo } = data.products;
      for (const product of nodes) items.push(...this.#flatten(product));
      logger.debug(`Shopify page ${page + 1}: ${nodes.length} products`, { total: items.length });
      if (!pageInfo.hasNextPage) break;
      cursor = pageInfo.endCursor;
    }

    logger.info(`Shopify: loaded ${items.length} variants`);
    return items;
  }

  #flatten(product) {
    const images = product.images.nodes.map((i) => i.url);
    const description = normaliseText(String(product.descriptionHtml ?? '').replace(/<[^>]+>/g, ' '));

    return product.variants.nodes.map((variant) => {
      const options = new Map(variant.selectedOptions.map((o) => [o.name.toLowerCase(), o.value]));
      const size = SIZE_OPTIONS.map((n) => options.get(n)).find(Boolean) ?? '';
      const colour = COLOUR_OPTIONS.map((n) => options.get(n)).find(Boolean) ?? '';

      return makeItem({
        sku: variant.sku ?? '',
        source: 'shopify',
        sourceId: product.id,
        variantId: variant.id,
        url: product.onlineStoreUrl ?? '',
        title: product.title,
        description,
        price: Number.parseFloat(variant.price),
        currency: this.currency,
        quantity: variant.inventoryQuantity ?? 0,
        status: product.status === 'ACTIVE' ? 'active' : product.status === 'DRAFT' ? 'draft' : 'hidden',
        brand: product.vendor ?? '',
        size,
        colour,
        category: product.productType ?? '',
        images,
        updatedAt: product.updatedAt,
        raw: { inventoryItemId: variant.inventoryItem?.id ?? '' },
      });
    });
  }

  // --- writes ---------------------------------------------------------------

  async setPrice(item, price) {
    const data = await this.graphql(`
      mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
        productVariantsBulkUpdate(productId: $productId, variants: $variants) {
          productVariants { id price }
          userErrors { field message }
        }
      }
    `, {
      productId: item.sourceId,
      variants: [{ id: item.variantId, price: String(price) }],
    });
    assertNoUserErrors(data.productVariantsBulkUpdate);
    return data.productVariantsBulkUpdate.productVariants[0];
  }

  async setQuantity(item, quantity, locationId) {
    const inventoryItemId = item.raw?.inventoryItemId;
    if (!inventoryItemId) throw new Error(`No inventory item id for ${item.sku || item.title}`);
    if (!locationId) throw new Error('No Shopify location selected — set one in Options.');

    const data = await this.graphql(`
      mutation($input: InventorySetQuantitiesInput!) {
        inventorySetQuantities(input: $input) {
          userErrors { field message }
        }
      }
    `, {
      input: {
        name: 'available',
        reason: 'correction',
        ignoreCompareQuantity: true,
        quantities: [{ inventoryItemId, locationId, quantity }],
      },
    });
    assertNoUserErrors(data.inventorySetQuantities);
  }

  async updateContent(item, changes) {
    const input = { id: item.sourceId };
    for (const change of changes) {
      if (change.field === 'title') input.title = change.to;
      if (change.field === 'description') input.descriptionHtml = change.to;
      if (change.field === 'brand') input.vendor = change.to;
      // size / colour live on variant options; handled by the option writer in a
      // later phase because it may require restructuring the product's options.
    }
    if (Object.keys(input).length === 1) return null;

    const data = await this.graphql(`
      mutation($input: ProductInput!) {
        productUpdate(input: $input) {
          product { id title }
          userErrors { field message }
        }
      }
    `, { input });
    assertNoUserErrors(data.productUpdate);
    return data.productUpdate.product;
  }

  async archive(item) {
    const data = await this.graphql(`
      mutation($input: ProductInput!) {
        productUpdate(input: $input) {
          product { id status }
          userErrors { field message }
        }
      }
    `, { input: { id: item.sourceId, status: 'ARCHIVED' } });
    assertNoUserErrors(data.productUpdate);
    return data.productUpdate.product;
  }
}

function assertNoUserErrors(result) {
  const errors = result?.userErrors ?? [];
  if (errors.length) {
    throw new Error(errors.map((e) => `${(e.field ?? []).join('.')}: ${e.message}`).join('; '));
  }
}
