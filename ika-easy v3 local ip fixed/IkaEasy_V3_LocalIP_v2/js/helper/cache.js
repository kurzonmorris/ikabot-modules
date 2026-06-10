class CacheService{
    constructor(){
        this.cache = {};
    }

    //TODO: localstorage
    getModuleCache(module){
        if(!(module in this.cache)){
            this.cache[module] = {};
        }
        return this.cache[module];
    }
}
export default new CacheService();
