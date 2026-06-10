class HttpClient {
    constructor(){
    }

    httpNotify(url, type, result){
        window.postMessage({
            type: 'FROM_IKAEASY_V3',
            cmd: 'ajax',
            request: {
                type: type,
                data: result,
                url: url
            }
        });
    }

    ikariam(path, params) {
        return new Promise(resolve => {
            params.actionRequest = Front.data.actionRequest;
            params.ajax = 1;

            let url = `${path}?${$.param(params)}`;

            let cb = (result) => {
                this.httpNotify(url, 'get', result);
                resolve(result);
            };

            $.ajax({
                url: url,
                async: true,
                dataType: "json",
                error: cb,
                success: cb
            });
        })
    }
}

export default new HttpClient();
