const fs = require('fs');

const input = process.argv[2];
const output = process.argv[3];

if (!input || !output) {
  throw new Error('Usage: node patch-razorpay-correlation.js <input> <output>');
}

const collection = JSON.parse(fs.readFileSync(input, 'utf8').replace(/^\uFEFF/, ''));

// Newman does not reliably expand collection variables whose values are other
// variable expressions when it builds a Basic Auth header. Point authentication
// directly at the environment variables instead.
if (collection.auth?.type === 'basic' && Array.isArray(collection.auth.basic)) {
  for (const entry of collection.auth.basic) {
    if (entry.key === 'username') entry.value = '{{api_key}}';
    if (entry.key === 'password') entry.value = '{{api_secret}}';
  }
}

let changedAuthBlocks = 0;
function patchRequestAuth(items) {
  for (const item of items || []) {
    if (item.request?.auth?.type === 'basic' && Array.isArray(item.request.auth.basic)) {
      for (const entry of item.request.auth.basic) {
        if (entry.key === 'username') entry.value = '{{api_key}}';
        if (entry.key === 'password') entry.value = '{{api_secret}}';
      }
      changedAuthBlocks += 1;
    }
    patchRequestAuth(item.item);
  }
}
patchRequestAuth(collection.item);

const orders = collection.item?.find((item) => item.name === 'Orders APIs');

if (!orders) {
  throw new Error('Orders APIs folder was not found');
}

const createOrder = orders.item?.find((item) => item.name === 'Create an Order');
if (!createOrder) {
  throw new Error('Create an Order request was not found');
}

createOrder.event = (createOrder.event || []).filter(
  (event) => event.listen !== 'test' || event.script?.name !== 'Capture order_id for correlation',
);
createOrder.event.push({
  listen: 'test',
  script: {
    name: 'Capture order_id for correlation',
    type: 'text/javascript',
    exec: [
      'pm.test("Create order returned 200", function () {',
      '    pm.response.to.have.status(200);',
      '});',
      '',
      'const response = pm.response.json();',
      'pm.test("Order ID was returned", function () {',
      '    pm.expect(response.id).to.be.a("string").and.not.empty;',
      '});',
      'pm.collectionVariables.set("order_id", response.id);',
    ],
  },
});

let changedUrls = 0;
for (const requestItem of orders.item || []) {
  const url = requestItem.request?.url;
  if (!url) continue;

  if (typeof url.raw === 'string' && url.raw.includes('{order_id}')) {
    url.raw = url.raw.replaceAll('{order_id}', '{{order_id}}');
    changedUrls += 1;
  }

  if (Array.isArray(url.path)) {
    url.path = url.path.map((segment) =>
      segment === '{order_id}' ? '{{order_id}}' : segment,
    );
  }
}

collection.variable = collection.variable || [];
const orderIdVariable = collection.variable.find((variable) => variable.key === 'order_id');
if (orderIdVariable) {
  orderIdVariable.value = '';
} else {
  collection.variable.push({ key: 'order_id', value: '', type: 'string' });
}

collection.info.name = `${collection.info.name} - Correlated`;
fs.writeFileSync(output, `${JSON.stringify(collection, null, 2)}\n`, 'utf8');
console.log(`Created: ${output}`);
console.log(`Changed order_id URLs: ${changedUrls}`);
console.log('Added Create an Order post-response capture: yes');
console.log('Changed Basic Auth to direct environment variables: yes');
console.log(`Changed request-level Basic Auth blocks: ${changedAuthBlocks}`);
